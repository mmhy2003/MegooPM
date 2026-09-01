"""Tests for the LLM client seam.

litellm is never imported at module scope (it costs 3.49s), so these inject a
fake module into ``sys.modules`` before calling. That also gives the telemetry
assertion something real to check.
"""

from __future__ import annotations

import subprocess
import sys
import types

import pytest
from app.services.llm import (
    LlmCheckResult,
    LlmConfig,
    LlmError,
    check_connection,
    complete,
    scrub_secrets,
)


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)


class _Response:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]


@pytest.fixture
def fake_litellm(monkeypatch):
    """A stand-in litellm, with the defaults the real package actually ships."""
    module = types.ModuleType("litellm")
    module.telemetry = True  # the real default — the service must flip it
    module.suppress_debug_info = False
    module.calls = []

    async def _acompletion(**kwargs):
        module.calls.append(kwargs)
        if getattr(module, "raises", None) is not None:
            raise module.raises
        return _Response(getattr(module, "reply", "OK"))

    module.acompletion = _acompletion
    monkeypatch.setitem(sys.modules, "litellm", module)
    return module


# --- The lazy-import guard -------------------------------------------------


def test_litellm_is_not_imported_when_the_app_is() -> None:
    """3.49s to import vs 0.84s for the whole app — 4x startup on every process.

    Runs in a subprocess because this test session may have imported litellm
    from another test, which would make an in-process check meaningless.
    """
    code = "import app.main, sys; print('litellm' in sys.modules)"
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert proc.stdout.strip() == "False", (
        "litellm reached module scope — every process now pays 3.49s at boot"
    )


# --- Scrubbing -------------------------------------------------------------


def test_scrubber_removes_the_configured_key() -> None:
    key = "sk-EXAMPLE-not-a-real-credential-1"
    raw = f"AuthenticationError: invalid key {key} for model gpt-4o"
    scrubbed = scrub_secrets(raw, key=key)
    assert key not in scrubbed
    assert "***" in scrubbed
    # Still useful: the parts that explain the failure survive.
    assert "AuthenticationError" in scrubbed
    assert "gpt-4o" in scrubbed


# Deliberately NOT realistic. These only need to match the scrubber's shape
# patterns, and a genuine-looking `sk_live_...` here is blocked by GitHub's push
# protection as a Stripe key — which cost one rewritten history to learn. Keep
# them obviously synthetic.
@pytest.mark.parametrize(
    "secret",
    [
        "sk-EXAMPLE-not-a-real-credential-1",
        "sk_EXAMPLE_not_a_real_credential_2",
        "gsk_EXAMPLE_not_a_real_credential_3",
        "AIzaEXAMPLEnotarealcredential0000000",
    ],
)
def test_scrubber_removes_key_shaped_strings_it_was_not_given(secret: str) -> None:
    """The stored key is the main defence; shape matching catches the rest."""
    assert secret not in scrub_secrets(f"boom: {secret} at line 2")


def test_scrubber_removes_bearer_tokens_but_keeps_the_word() -> None:
    scrubbed = scrub_secrets("headers: {'Authorization': 'Bearer sk-topsecretvalue'}")
    assert "sk-topsecretvalue" not in scrubbed
    assert "Bearer" in scrubbed


def test_scrubber_leaves_ordinary_error_text_alone() -> None:
    """Over-scrubbing makes an error useless, which is its own failure."""
    raw = "Connection refused to http://localhost:11434 for model ollama/llama3"
    assert scrub_secrets(raw) == raw


# --- complete --------------------------------------------------------------


async def test_complete_returns_the_message_text(fake_litellm) -> None:
    fake_litellm.reply = "hello there"
    out = await complete(LlmConfig(model="gpt-4o", api_key="sk-x"), prompt="hi")
    assert out == "hello there"


async def test_complete_passes_credentials_explicitly(fake_litellm) -> None:
    await complete(
        LlmConfig(model="gpt-4o", api_key="sk-x", api_base="https://gw.example.com"),
        prompt="hi",
        timeout=12.0,
    )
    call = fake_litellm.calls[0]
    assert call["model"] == "gpt-4o"
    assert call["api_key"] == "sk-x"
    assert call["api_base"] == "https://gw.example.com"
    assert call["timeout"] == 12.0


async def test_complete_omits_a_missing_key_rather_than_sending_empty(fake_litellm) -> None:
    """A local runner needs no key; an empty string is not the same as none."""
    await complete(LlmConfig(model="ollama/llama3"), prompt="hi")
    assert fake_litellm.calls[0]["api_key"] is None


async def test_complete_sends_the_system_prompt_first(fake_litellm) -> None:
    await complete(LlmConfig(model="gpt-4o"), prompt="hi", system="be terse")
    messages = fake_litellm.calls[0]["messages"]
    assert messages[0] == {"role": "system", "content": "be terse"}
    assert messages[1] == {"role": "user", "content": "hi"}


async def test_complete_disables_telemetry(fake_litellm) -> None:
    """litellm phones home by default; a proxy manager must not."""
    await complete(LlmConfig(model="gpt-4o"), prompt="hi")
    assert fake_litellm.telemetry is False
    assert fake_litellm.suppress_debug_info is True


async def test_complete_wraps_provider_failures_and_scrubs_them(fake_litellm) -> None:
    key = "sk-EXAMPLE-not-a-real-credential-1"
    fake_litellm.raises = RuntimeError(f"401 from provider using {key}")
    with pytest.raises(LlmError) as excinfo:
        await complete(LlmConfig(model="gpt-4o", api_key=key), prompt="hi")
    assert key not in str(excinfo.value)


# --- check_connection ------------------------------------------------------


async def test_check_connection_reports_success(fake_litellm) -> None:
    fake_litellm.reply = "OK"
    result = await check_connection(LlmConfig(model="gpt-4o", api_key="sk-x"))
    assert isinstance(result, LlmCheckResult)
    assert result.ok is True
    assert result.reply == "OK"
    assert result.model == "gpt-4o"
    assert result.error == ""
    assert result.latency_ms >= 0


async def test_check_connection_reports_failure_without_raising(fake_litellm) -> None:
    """A failed probe is a result, not an exception — the route returns 200."""
    key = "sk-EXAMPLE-not-a-real-credential-1"
    fake_litellm.raises = RuntimeError(f"bad key {key}")
    result = await check_connection(LlmConfig(model="gpt-4o", api_key=key))
    assert result.ok is False
    assert result.reply == ""
    assert result.error
    assert key not in result.error


async def test_check_connection_caps_the_probe(fake_litellm) -> None:
    """The probe should cost a handful of tokens, not a paragraph."""
    await check_connection(LlmConfig(model="gpt-4o"))
    assert fake_litellm.calls[0]["max_tokens"] <= 16
