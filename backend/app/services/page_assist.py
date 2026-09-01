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

import re

from app.services.llm import LlmConfig, complete

# An instruction is a sentence or two. An unbounded field is just an unmetered
# path into a paid API.
MAX_INSTRUCTION_CHARS = 2000

# The most an *elided* document may be. The browser checks this too, so the
# operator gets a sentence rather than a 422; this copy is the enforcement.
MAX_ASSIST_HTML_BYTES = 200 * 1024

# A generation from a strong model runs 20-60 seconds, and the editor shows a
# spinner with a cancel button throughout. Cutting it off at the usual 60 would
# fail the exact requests this feature exists for.
ASSIST_TIMEOUT_SECONDS = 120.0

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


async def assist_page(
    config: LlmConfig,
    *,
    instruction: str,
    html: str,
    timeout: float = ASSIST_TIMEOUT_SECONDS,
) -> str:
    """Run one page edit or generation and return a cleaned HTML document.

    Raises :class:`~app.services.llm.LlmError` — already scrubbed — for anything
    the provider or the transport does wrong.
    """
    raw = await complete(
        config,
        prompt=build_user_message(instruction, html),
        system=SYSTEM_PROMPT,
        timeout=timeout,
    )
    return strip_document_fences(raw)


__all__ = [
    "ASSIST_TIMEOUT_SECONDS",
    "MAX_ASSIST_HTML_BYTES",
    "MAX_INSTRUCTION_CHARS",
    "SYSTEM_PROMPT",
    "assist_page",
    "build_user_message",
    "strip_document_fences",
]
