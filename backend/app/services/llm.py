"""The application's only door to an LLM provider.

Everything that talks to a language model goes through here, so the awkward
parts of the dependency are handled in exactly one place:

**litellm is imported inside the functions, never at module scope.** It costs
3.49 seconds to import, against 0.84s for the entire application — at module
level that is a 4x startup penalty on the API process, the Celery worker and
beat, paid on every boot whether or not anyone has enabled the feature.
``tests/test_llm_service.py`` pins this, because the regression is invisible:
someone adds a convenient top-level import for a type annotation and every
process just gets slower.

**Telemetry is switched off on every call.** ``litellm.telemetry`` is ``True``
out of the box. A product whose job is controlling what reaches the network
does not ship a dependency that phones home unasked.

**Provider errors are scrubbed before they leave.** Some SDKs put the whole
request — headers included — in their error text, so a raw message can carry
the API key into an admin's browser.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass


class LlmNotConfiguredError(Exception):
    """Raised when an LLM call is attempted with no usable configuration."""


class LlmError(Exception):
    """A provider or transport failure. The message is already scrubbed."""


@dataclass(frozen=True, slots=True)
class LlmConfig:
    """Everything one call needs. Never holds a session, so it is trivial to fake."""

    model: str
    api_key: str | None = None
    api_base: str | None = None


@dataclass(frozen=True, slots=True)
class LlmCheckResult:
    """The outcome of a probe. A failure is a result here, not an exception."""

    ok: bool
    model: str
    reply: str = ""
    error: str = ""
    latency_ms: int = 0


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool the model asked to run. ``arguments`` is raw JSON, unparsed."""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class LlmTurn:
    """One assistant turn, normalised so callers never touch a litellm object.

    ``message`` is the turn as a plain dict, ready to append back into the
    conversation — a tool loop has to replay it verbatim on the next call.
    """

    content: str
    tool_calls: tuple[ToolCall, ...]
    message: dict


# Key shapes worth catching even when the configured key is unknown — a
# provider may echo a *different* credential than the one we sent. Deliberately
# narrow: over-scrubbing turns a useful error into a row of asterisks, which is
# its own kind of failure.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:sk|pk|rk|gsk|xai)[-_][A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bAIza[A-Za-z0-9_\-]{20,}"),
)
_BEARER = re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._\-]{8,}")

# The probe asks for one word and caps the answer, so checking a connection
# costs a handful of tokens rather than a paragraph.
_PROBE_PROMPT = "Reply with the single word: OK"
_PROBE_MAX_TOKENS = 16


def scrub_secrets(text: str, *, key: str | None = None) -> str:
    """Redact credentials from provider error text.

    The configured key is the main defence — an exact replacement, so it goes
    whatever shape it has. Pattern matching is the backstop for credentials we
    were never given.
    """
    if key:
        text = text.replace(key, "***")
    text = _BEARER.sub(r"\1 ***", text)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("***", text)
    return text


def _litellm():
    """Import litellm on demand and pin the settings we refuse to ship as-is.

    Kept in one place so the import cost and the telemetry default are handled
    identically on every path. Python caches the module, so only the first call
    in a process pays.
    """
    import litellm

    litellm.telemetry = False
    litellm.suppress_debug_info = True
    return litellm


async def complete(
    config: LlmConfig,
    *,
    prompt: str,
    system: str | None = None,
    max_tokens: int | None = None,
    timeout: float = 60.0,
) -> str:
    """Run one completion and return the message text.

    Raises :class:`LlmError` — already scrubbed — for anything the provider or
    the transport does wrong.
    """
    if not config.model:
        raise LlmNotConfiguredError("No model configured")

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    litellm = _litellm()
    try:
        response = await litellm.acompletion(
            model=config.model,
            messages=messages,
            # Explicit, always. With no key litellm falls back to its own
            # environment resolution, which is what makes a keyless local
            # runner work — and is why the UI says so on the field.
            api_key=config.api_key or None,
            api_base=config.api_base or None,
            max_tokens=max_tokens,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — every provider raises its own type
        raise LlmError(scrub_secrets(str(exc), key=config.api_key)) from None

    return (response.choices[0].message.content or "").strip()


async def complete_with_tools(
    config: LlmConfig,
    *,
    messages: list[dict],
    tools: list[dict],
    timeout: float = 60.0,
) -> LlmTurn:
    """One turn of a tool-calling conversation.

    Takes and returns whole messages rather than a prompt, because a tool loop
    has to carry the conversation forward. Everything litellm-shaped stops here:
    the caller gets a :class:`LlmTurn` of plain data.

    Raises :class:`LlmError` — already scrubbed — for anything the provider or
    the transport does wrong, including refusing ``tools`` outright.
    """
    if not config.model:
        raise LlmNotConfiguredError("No model configured")

    litellm = _litellm()
    try:
        response = await litellm.acompletion(
            model=config.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            api_key=config.api_key or None,
            api_base=config.api_base or None,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — every provider raises its own type
        raise LlmError(scrub_secrets(str(exc), key=config.api_key)) from None

    message = response.choices[0].message
    raw_calls = getattr(message, "tool_calls", None) or ()
    calls = tuple(
        ToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments) for c in raw_calls
    )
    # Rebuilt from the fields we need rather than serialised from the provider
    # object. A provider's own dump can carry extras that the next request
    # rejects, and this is the exact shape the chat API expects when a tool
    # conversation replays the assistant turn.
    replay: dict = {"role": "assistant", "content": message.content}
    if calls:
        replay["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": c.arguments},
            }
            for c in calls
        ]
    return LlmTurn(content=(message.content or "").strip(), tool_calls=calls, message=replay)


async def check_connection(config: LlmConfig, *, timeout: float = 30.0) -> LlmCheckResult:
    """Probe the configuration end to end and report, never raise.

    A minimal completion is the only thing that proves the *whole* path —
    credentials, base URL, model name, and the provider actually answering.
    """
    # Warm the import before starting the clock. litellm costs 3.49s to load
    # and only the first call in a process pays it — but that call is usually
    # this one, and folding it into the reported latency would tell an operator
    # their local Ollama took three and a half seconds to answer.
    _litellm()

    started = time.perf_counter()
    try:
        reply = await complete(
            config,
            prompt=_PROBE_PROMPT,
            max_tokens=_PROBE_MAX_TOKENS,
            timeout=timeout,
        )
    except (LlmError, LlmNotConfiguredError) as exc:
        return LlmCheckResult(
            ok=False,
            model=config.model,
            error=str(exc),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    return LlmCheckResult(
        ok=True,
        model=config.model,
        reply=reply,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


__all__ = [
    "LlmCheckResult",
    "LlmConfig",
    "LlmError",
    "LlmNotConfiguredError",
    "LlmTurn",
    "ToolCall",
    "check_connection",
    "complete",
    "complete_with_tools",
    "scrub_secrets",
]
