"""Tests for AI page assistance — the prompt and the output hygiene.

litellm is never imported here: ``complete`` is patched, so these stay fast and
have nothing to do with any provider.
"""

from __future__ import annotations

import pytest
from app.services.llm import LlmConfig
from app.services.page_assist import (
    ASSIST_TIMEOUT_SECONDS,
    SYSTEM_PROMPT,
    assist_page,
    build_user_message,
    strip_document_fences,
)

DOC = "<!doctype html>\n<html><body><h1>hi</h1></body></html>"


# --- Output hygiene --------------------------------------------------------


def test_passes_a_clean_document_through() -> None:
    assert strip_document_fences(DOC) == DOC


@pytest.mark.parametrize("tag", ["", "html", "HTML"])
def test_strips_a_markdown_fence(tag: str) -> None:
    """Models add these constantly, whatever the system prompt says."""
    assert strip_document_fences(f"```{tag}\n{DOC}\n```") == DOC


def test_strips_prose_before_the_document() -> None:
    assert strip_document_fences(f"Sure! Here is the updated page:\n\n{DOC}") == DOC


def test_strips_prose_after_the_document() -> None:
    assert strip_document_fences(f"{DOC}\n\nLet me know if you want changes!") == DOC


def test_strips_a_fence_and_prose_together() -> None:
    messy = f"Here you go:\n\n```html\n{DOC}\n```\n\nHope that helps."
    assert strip_document_fences(messy) == DOC


def test_finds_a_document_that_starts_with_doctype_in_any_case() -> None:
    doc = "<!DOCTYPE html>\n<html></html>"
    assert strip_document_fences(f"note\n{doc}") == doc


def test_returns_a_fragment_unchanged_rather_than_rejecting_it() -> None:
    """An instruction may legitimately have asked for a fragment, and the
    operator can see and revert whatever comes back."""
    assert strip_document_fences("```html\n<p>just a paragraph</p>\n```") == (
        "<p>just a paragraph</p>"
    )


# --- The prompt ------------------------------------------------------------


def test_the_system_prompt_states_the_rules_that_matter() -> None:
    lowered = SYSTEM_PROMPT.lower()
    assert "megoopm_image" in lowered  # placeholders must survive
    assert "no external" in lowered or "self-contained" in lowered
    assert "markdown" in lowered or "fence" in lowered


def test_the_user_message_carries_the_instruction_and_the_document() -> None:
    message = build_user_message("make the heading bigger", DOC)
    assert "make the heading bigger" in message
    assert DOC in message


def test_the_user_message_says_so_when_there_is_no_document() -> None:
    message = build_user_message("a 503 maintenance page", "   ")
    assert "a 503 maintenance page" in message
    assert "no current document" in message.lower()


# --- assist_page -----------------------------------------------------------


@pytest.fixture
def captured(monkeypatch):
    """Replace the LLM round trip: record what it was asked, control what it says."""
    import app.services.page_assist as page_assist

    calls: list[dict] = []
    reply = {"value": DOC}

    async def _complete(config, *, prompt, system=None, max_tokens=None, timeout=60.0):
        calls.append({"config": config, "prompt": prompt, "system": system, "timeout": timeout})
        return reply["value"]

    monkeypatch.setattr(page_assist, "complete", _complete)
    return calls, reply


async def test_assist_page_returns_the_document(captured) -> None:
    """The generation path: an empty document means there is nothing to edit."""
    calls, _ = captured
    result = await assist_page(
        LlmConfig(model="gpt-4o", api_key="sk-EXAMPLE-not-a-real-credential-1"),
        instruction="make it blue",
        html="",
    )
    assert result.html == DOC
    assert result.mode == "generate"
    assert calls[0]["system"] == SYSTEM_PROMPT
    assert "make it blue" in calls[0]["prompt"]


async def test_assist_page_strips_what_the_model_wrapped(captured) -> None:
    _, reply = captured
    reply["value"] = f"```html\n{DOC}\n```"
    result = await assist_page(LlmConfig(model="gpt-4o"), instruction="tidy it", html="")
    assert result.html == DOC
    assert result.mode == "generate"


async def test_assist_page_allows_a_long_generation(captured) -> None:
    """A full page from a strong model takes 20-60s, a large one several minutes."""
    calls, _ = captured
    await assist_page(LlmConfig(model="gpt-4o"), instruction="write one", html="")
    assert calls[0]["timeout"] >= 240.0


def test_the_timeout_stays_below_the_proxys() -> None:
    """Whichever timeout fires first owns the error.

    nginx sets proxy_read_timeout to 300s (infra/nginx/nginx.conf). If this one
    ever crept above that, the proxy would sever the connection first and the
    operator would get "Failed to fetch" instead of a scrubbed, readable
    message. That inversion is exactly what shipped and had to be fixed.
    """
    proxy_read_timeout = 300.0
    assert ASSIST_TIMEOUT_SECONDS < proxy_read_timeout


# --- The tool loop ---------------------------------------------------------

import json  # noqa: E402

from app.services.llm import LlmError, LlmTurn, ToolCall  # noqa: E402
from app.services.page_assist import (  # noqa: E402
    MAX_TOOL_TURNS,
    AssistResult,
)

PAGE = "\n".join(
    ["<!doctype html>", "<html>", "  <body>", "    <h1>Old</h1>", "  </body>", "</html>"]
)


def _tool_turn(name: str, **arguments) -> LlmTurn:
    """An assistant turn that asks for one tool call."""
    return LlmTurn(
        content="",
        tool_calls=(ToolCall(id="c1", name=name, arguments=json.dumps(arguments)),),
        message={"role": "assistant", "content": None},
    )


def _done_turn(text: str = "Done.") -> LlmTurn:
    return LlmTurn(content=text, tool_calls=(), message={"role": "assistant", "content": text})


@pytest.fixture
def scripted(monkeypatch):
    """Drive the loop with a fixed sequence of model turns."""
    import app.services.page_assist as page_assist

    state: dict = {"turns": [], "seen": []}

    async def _with_tools(config, *, messages, tools, timeout=60.0):
        state["seen"].append({"messages": list(messages), "tools": tools})
        return state["turns"].pop(0)

    monkeypatch.setattr(page_assist, "complete_with_tools", _with_tools)
    return state


async def test_the_loop_edits_the_document_it_was_given(scripted) -> None:
    scripted["turns"] = [
        _tool_turn("grep", pattern="<h1"),
        _tool_turn("replace_lines", start=4, end=4, text="    <h1>New</h1>"),
        _done_turn(),
    ]
    result = await assist_page(LlmConfig(model="gpt-4o"), instruction="rename it", html=PAGE)

    assert isinstance(result, AssistResult)
    assert result.mode == "tools"
    assert result.truncated is False
    assert "<h1>New</h1>" in result.html
    assert "<h1>Old</h1>" not in result.html
    assert len(result.changes) == 1
    assert result.changes[0].before == "    <h1>Old</h1>"


async def test_the_opening_message_carries_the_numbered_document(scripted, captured) -> None:
    scripted["turns"] = [_done_turn()]
    await assist_page(LlmConfig(model="gpt-4o"), instruction="look", html=PAGE)
    opening = scripted["seen"][0]["messages"][-1]["content"]
    assert "1 |" in opening
    assert "<!doctype html>" in opening


async def test_tool_results_come_back_as_tool_messages(scripted, captured) -> None:
    """A provider will reject a conversation that replies to a call any other way."""
    scripted["turns"] = [_tool_turn("grep", pattern="<h1"), _done_turn()]
    await assist_page(LlmConfig(model="gpt-4o"), instruction="look", html=PAGE)

    second_call = scripted["seen"][1]["messages"]
    tool_messages = [m for m in second_call if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "c1"
    assert "<h1>Old</h1>" in tool_messages[0]["content"]


async def test_an_unknown_tool_is_reported_to_the_model_not_raised(scripted, captured) -> None:
    scripted["turns"] = [_tool_turn("delete_everything"), _done_turn()]
    await assist_page(LlmConfig(model="gpt-4o"), instruction="look", html=PAGE)
    tool_message = [m for m in scripted["seen"][1]["messages"] if m.get("role") == "tool"][0]
    assert "unknown tool" in tool_message["content"].lower()


async def test_malformed_tool_arguments_are_reported_to_the_model(scripted, captured) -> None:
    bad = LlmTurn(
        content="",
        tool_calls=(ToolCall(id="c1", name="grep", arguments="{not json"),),
        message={"role": "assistant", "content": None},
    )
    scripted["turns"] = [bad, _done_turn()]
    await assist_page(LlmConfig(model="gpt-4o"), instruction="look", html=PAGE)
    tool_message = [m for m in scripted["seen"][1]["messages"] if m.get("role") == "tool"][0]
    assert "error" in tool_message["content"].lower()


async def test_the_turn_cap_stops_a_model_that_never_finishes(scripted) -> None:
    """Applied, not discarded — the operator paid for the work that got done."""
    scripted["turns"] = [_tool_turn("replace_lines", start=4, end=4, text="    <h1>New</h1>")] + [
        _tool_turn("grep", pattern="x") for _ in range(MAX_TOOL_TURNS)
    ]

    result = await assist_page(LlmConfig(model="gpt-4o"), instruction="loop", html=PAGE)
    assert result.truncated is True
    assert result.mode == "tools"
    assert "<h1>New</h1>" in result.html
    assert len(scripted["seen"]) == MAX_TOOL_TURNS


# --- Generation and fallback -----------------------------------------------


async def test_an_empty_document_generates_rather_than_editing(captured) -> None:
    """There is nothing to grep; the model writes a page."""
    calls, _ = captured
    result = await assist_page(LlmConfig(model="gpt-4o"), instruction="a 503 page", html="")
    assert result.mode == "generate"
    assert result.changes == ()
    assert calls  # the plain completion path ran


async def test_a_model_that_never_calls_a_tool_falls_back_to_a_rewrite(scripted, captured) -> None:
    _, reply = captured
    reply["value"] = "<!doctype html>\n<html><body>rewritten</body></html>"
    scripted["turns"] = [_done_turn("I would change the heading.")]

    result = await assist_page(LlmConfig(model="gpt-4o"), instruction="edit", html=PAGE)
    assert result.mode == "rewrite"
    assert "rewritten" in result.html
    assert result.changes == ()


async def test_a_provider_that_rejects_tools_falls_back_to_a_rewrite(captured, monkeypatch) -> None:
    """MiniMax-style: tools may not be supported, and the feature must still work."""
    import app.services.page_assist as page_assist

    _, reply = captured
    reply["value"] = "<!doctype html>\n<html><body>rewritten</body></html>"

    async def _boom(config, *, messages, tools, timeout=60.0):
        raise LlmError("tools is not supported by this model")

    monkeypatch.setattr(page_assist, "complete_with_tools", _boom)

    result = await assist_page(LlmConfig(model="gpt-4o"), instruction="edit", html=PAGE)
    assert result.mode == "rewrite"
    assert "rewritten" in result.html


# --- the structural check ------------------------------------------------------


def test_check_html_is_offered_as_a_tool() -> None:
    from app.services.page_assist import TOOL_SCHEMAS

    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert "check_html" in names


async def test_the_model_can_check_the_document_mid_edit(scripted) -> None:
    """The tool reports on the document as the staged edits would leave it."""
    scripted["turns"] = [
        _tool_turn("replace_lines", start=4, end=4, text="    <h1>New"),
        _tool_turn("check_html"),
        # Having been told, it restages the same range — a revision, not a
        # collision — and the gate finds nothing left to complain about.
        _tool_turn("replace_lines", start=4, end=4, text="    <h1>New</h1>"),
        _done_turn(),
    ]
    result = await assist_page(LlmConfig(model="gpt-4o"), instruction="rename", html=PAGE)
    assert "<h1>New</h1>" in result.html

    tool_replies = [
        m["content"]
        for seen in scripted["seen"]
        for m in seen["messages"]
        if m.get("role") == "tool"
    ]
    assert any("h1" in reply for reply in tool_replies)


async def test_a_broken_result_earns_one_repair_round(scripted) -> None:
    # The model leaves an unclosed <h1>, is told, and fixes it.
    scripted["turns"] = [
        _tool_turn("replace_lines", start=4, end=4, text="    <h1>New"),
        _done_turn(),
        _tool_turn("replace_lines", start=4, end=4, text="    <h1>New</h1>"),
        _done_turn(),
    ]

    result = await assist_page(LlmConfig(model="gpt-4o"), instruction="rename", html=PAGE)

    assert "<h1>New</h1>" in result.html
    # The repair round is a real message naming the fault, not a silent retry.
    last_opening = scripted["seen"][-1]["messages"]
    assert any("h1" in str(m.get("content", "")) for m in last_opening)


async def test_a_clean_result_earns_no_repair_round(scripted) -> None:
    scripted["turns"] = [
        _tool_turn("replace_lines", start=4, end=4, text="    <h1>New</h1>"),
        _done_turn(),
    ]

    result = await assist_page(LlmConfig(model="gpt-4o"), instruction="rename", html=PAGE)

    assert "<h1>New</h1>" in result.html
    # Both scripted turns consumed and no more asked for: no repair happened.
    assert scripted["turns"] == []


async def test_a_page_still_broken_after_the_repair_is_returned_anyway(scripted) -> None:
    """One repair round, not a loop. A model that cannot fix it in one pass
    will thrash, and the operator still has the preview and the undo."""
    scripted["turns"] = [
        _tool_turn("replace_lines", start=4, end=4, text="    <h1>New"),
        _done_turn(),
        _done_turn(),
    ]

    result = await assist_page(LlmConfig(model="gpt-4o"), instruction="rename", html=PAGE)

    assert "<h1>New" in result.html
