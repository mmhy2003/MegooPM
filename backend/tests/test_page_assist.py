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
    calls, _ = captured
    out = await assist_page(
        LlmConfig(model="gpt-4o", api_key="sk-EXAMPLE-not-a-real-credential-1"),
        instruction="make it blue",
        html=DOC,
    )
    assert out == DOC
    assert calls[0]["system"] == SYSTEM_PROMPT
    assert "make it blue" in calls[0]["prompt"]


async def test_assist_page_strips_what_the_model_wrapped(captured) -> None:
    _, reply = captured
    reply["value"] = f"```html\n{DOC}\n```"
    out = await assist_page(LlmConfig(model="gpt-4o"), instruction="tidy it", html=DOC)
    assert out == DOC


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
