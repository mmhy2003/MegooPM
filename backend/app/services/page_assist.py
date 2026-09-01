"""Ask a language model to write or revise a custom page.

Two jobs, both awkward enough to deserve their own module:

**The prompt.** A custom page is served straight off nginx, possibly on a box
with no egress, so a generated page must be self-contained — the same rule the
bundled congratulations page follows. And the browser has replaced every
embedded image with a ``MEGOOPM_IMAGE_n`` placeholder before sending, so those
tokens must come back untouched or the images cannot be restored.

**Output hygiene.** Models wrap code in markdown fences constantly, whatever
they are told, and add a sentence of preamble on top. Left alone that renders as
literal backticks on the served page, so :func:`strip_document_fences` removes
it. That function is where most of this module's tests live, because it is the
difference between a working page and a visibly broken one.

This module does not import litellm. :func:`~app.services.llm.complete` is the
only door to a provider, and it imports litellm inside its own functions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.services.llm import LlmConfig, LlmError, complete, complete_with_tools
from app.services.page_tools import EditDocument, StagedEdit

# An instruction is a sentence or two. An unbounded field is just an unmetered
# path into a paid API.
MAX_INSTRUCTION_CHARS = 2000

# The most an *elided* document may be. The browser checks this too, so the
# operator gets a sentence rather than a 422; this copy is the enforcement.
MAX_ASSIST_HTML_BYTES = 200 * 1024

# A generation from a strong model runs 20-60 seconds, and a large page with a
# detailed instruction can run several minutes. The editor shows a spinner with
# a cancel button throughout, so a long ceiling costs the operator nothing.
#
# This must stay BELOW the proxy's own proxy_read_timeout (300s in
# infra/nginx/nginx.conf). Whichever timeout fires first owns the error, and
# only this one produces a scrubbed, readable message -- the proxy produces a
# 504 the browser reports as "Failed to fetch".
ASSIST_TIMEOUT_SECONDS = 240.0

SYSTEM_PROMPT = """You edit and write single-file HTML pages for MegooPM, a \
reverse-proxy manager. These pages are served directly by nginx.

Rules:
- Reply with the HTML document and nothing else. No explanation, no commentary.
- Never wrap the output in markdown code fences.
- The document must be entirely self-contained: no external stylesheets, \
scripts, fonts or images, and no network requests of any kind. The page may be \
served on a machine with no internet access.
- Any src value of the form data:<type>;base64,MEGOOPM_IMAGE_<number> is a \
placeholder for an image already in the page. Reproduce it exactly, character \
for character, unless the instruction asks for that image to be removed. Never \
invent a new MEGOOPM_IMAGE placeholder.
- Keep the document's existing structure and content except where the \
instruction asks otherwise."""

# Each turn is a round trip. Eight is the ceiling that stops a confused model
# wandering, and at 5-20s a turn it still fits inside ASSIST_TIMEOUT_SECONDS.
MAX_TOOL_TURNS = 8

TOOL_SYSTEM_PROMPT = """\
You edit single-file HTML pages for MegooPM, a reverse-proxy manager,
using the tools provided.

How to work:
- The document is shown to you with line numbers. Use grep to locate text,
  and read_lines to see more around it.
- Use replace_lines to change what needs changing. Change as little as
  possible; leave every line the instruction does not require you to touch.
- Line numbers ALWAYS refer to the document as first shown to you. They
  never shift, however many edits you stage.
- To insert, replace a line with itself plus the new content. To delete,
  replace with empty text.
- grep matches literal text, not regular expressions.
- When the instruction is satisfied, reply with a short sentence and no
  further tool calls.

The page is served directly by nginx and must stay entirely self-contained:
no external stylesheets, scripts, fonts or images. Any src value of the form
data:<type>;base64,MEGOOPM_IMAGE_<number> is a placeholder for an image
already in the page - reproduce it exactly.
"""

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "Find lines containing a literal substring. Returns matching "
                "lines with their numbers and surrounding context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Literal text to find."},
                    "ignore_case": {"type": "boolean", "default": False},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_lines",
            "description": "Read an inclusive range of numbered lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "integer"},
                    "end": {"type": "integer"},
                },
                "required": ["start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_lines",
            "description": (
                "Stage a replacement of an inclusive line range. Line numbers "
                "refer to the original document and never shift."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "integer"},
                    "end": {"type": "integer"},
                    "text": {"type": "string", "description": "The replacement lines."},
                },
                "required": ["start", "end", "text"],
            },
        },
    },
]


@dataclass(frozen=True, slots=True)
class AssistResult:
    """What one assist produced, and by which route.

    ``mode`` is ``tools`` for a targeted edit, ``generate`` for a page written
    from nothing, and ``rewrite`` when the tool path could not be used and the
    whole document was regenerated instead.
    """

    html: str
    mode: str
    truncated: bool = False
    changes: tuple[StagedEdit, ...] = field(default_factory=tuple)


_FENCE_OPEN = re.compile(r"\A\s*```[A-Za-z0-9_+-]*[ \t]*\r?\n")
_FENCE_CLOSE = re.compile(r"\r?\n\s*```\s*\Z")
_DOC_START = re.compile(r"<!doctype\b|<html\b", re.IGNORECASE)


def strip_document_fences(text: str) -> str:
    """Remove the markdown fences and prose models wrap around HTML.

    If no ``<!doctype`` or ``<html`` is found the fence-stripped text is
    returned as it is, rather than rejected: a model that answers with a
    fragment has produced something the operator can see in both the editor and
    the preview and can revert with one click — and an instruction may
    legitimately have asked for a fragment.
    """
    out = text.strip()
    out = _FENCE_OPEN.sub("", out)
    out = _FENCE_CLOSE.sub("", out)
    out = out.strip()

    start = _DOC_START.search(out)
    if start is not None:
        out = out[start.start() :]

    # Trailing "Let me know if you want changes!" is the same problem as a
    # leading preamble, and just as easy to cut.
    end = out.lower().rfind("</html>")
    if end != -1:
        out = out[: end + len("</html>")]

    return out.strip()


def build_user_message(instruction: str, html: str) -> str:
    """The instruction plus the document, or a note that there is none."""
    if html.strip():
        return f"Instruction:\n{instruction}\n\nCurrent document:\n{html}"
    return (
        f"Instruction:\n{instruction}\n\n"
        "There is no current document. Write a complete one from scratch."
    )


def _run_tool(doc: EditDocument, call) -> str:
    """Dispatch one tool call.

    Every failure is a message, never an exception — the model reads the result
    and can correct itself inside the loop rather than failing the request.
    """
    try:
        args = json.loads(call.arguments or "{}")
    except ValueError:
        return "Error: arguments were not valid JSON. Send a JSON object."

    if call.name == "grep":
        return doc.grep(str(args.get("pattern", "")), ignore_case=bool(args.get("ignore_case")))
    if call.name == "read_lines":
        return doc.read_lines(int(args.get("start", 0)), int(args.get("end", 0)))
    if call.name == "replace_lines":
        return doc.replace_lines(
            int(args.get("start", 0)), int(args.get("end", 0)), str(args.get("text", ""))
        )
    return f"Error: unknown tool {call.name!r}. Available: grep, read_lines, replace_lines."


async def _rewrite(config: LlmConfig, instruction: str, html: str, timeout: float) -> str:
    """The original whole-document path, still used to generate and to fall back."""
    raw = await complete(
        config,
        prompt=build_user_message(instruction, html),
        system=SYSTEM_PROMPT,
        timeout=timeout,
    )
    return strip_document_fences(raw)


async def _edit_with_tools(
    config: LlmConfig, *, instruction: str, html: str, timeout: float
) -> AssistResult:
    """Drive the model through the document tools. Raises LlmError like any call."""
    doc = EditDocument(html)
    messages: list[dict] = [
        {"role": "system", "content": TOOL_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (f"Instruction:\n{instruction}\n\nDocument:\n{doc.numbered()}"),
        },
    ]

    truncated = True
    for _ in range(MAX_TOOL_TURNS):
        turn = await complete_with_tools(
            config, messages=messages, tools=TOOL_SCHEMAS, timeout=timeout
        )
        messages.append(turn.message)
        if not turn.tool_calls:
            truncated = False
            break
        for call in turn.tool_calls:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": _run_tool(doc, call),
                }
            )

    new_html, changes = doc.apply()
    return AssistResult(html=new_html, mode="tools", truncated=truncated, changes=changes)


async def assist_page(
    config: LlmConfig,
    *,
    instruction: str,
    html: str,
    timeout: float = ASSIST_TIMEOUT_SECONDS,
) -> AssistResult:
    """Edit the page with tools, or write one, or fall back to a rewrite.

    The fallback fires on an :class:`LlmError` *anywhere* in the loop, not only
    on the first turn. A provider that refuses ``tools`` and one that fails
    mid-conversation surface as the same scrubbed error, and telling them apart
    is not reliably possible — so both retry as a rewrite. If the provider is
    genuinely broken the rewrite raises the same error and the operator sees it.
    """
    if not html.strip():
        return AssistResult(
            html=await _rewrite(config, instruction, html, timeout), mode="generate"
        )

    try:
        result = await _edit_with_tools(config, instruction=instruction, html=html, timeout=timeout)
    except LlmError:
        return AssistResult(html=await _rewrite(config, instruction, html, timeout), mode="rewrite")

    if not result.changes:
        # The model answered in prose and never touched a tool. Nothing changed,
        # so give the operator the rewrite rather than a no-op.
        return AssistResult(html=await _rewrite(config, instruction, html, timeout), mode="rewrite")
    return result


__all__ = [
    "ASSIST_TIMEOUT_SECONDS",
    "MAX_TOOL_TURNS",
    "TOOL_SCHEMAS",
    "TOOL_SYSTEM_PROMPT",
    "AssistResult",
    "MAX_ASSIST_HTML_BYTES",
    "MAX_INSTRUCTION_CHARS",
    "SYSTEM_PROMPT",
    "assist_page",
    "build_user_message",
    "strip_document_fences",
]
