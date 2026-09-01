# LLM Integration Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator point MegooPM at a language model from the Settings page — model, API key, optional base URL — and prove the connection works with a probe, via the `litellm` package.

**Architecture:** Four columns join the existing `instance_settings` singleton, with the API key encrypted through the Fernet helper already used for CrowdSec and DNS credentials. A narrow `app/services/llm.py` is the only module that touches litellm, and it imports litellm *inside* its functions because the package costs 3.49s to import. `PATCH /api/v1/settings` splits into one route per settings group, since the default-site group's "mode is required" rule does not generalise.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 + Alembic, Pydantic v2, `cryptography` (Fernet), `litellm`; Next.js 16 + React 19 + base-ui + vitest on the frontend.

**Spec:** `docs/superpowers/specs/2026-09-01-llm-integration-design.md`

## Global Constraints

- **`litellm` is NEVER imported at module scope.** It costs **3.49s** to import against **0.84s** for the whole application — a 4x startup penalty on the API, the Celery worker and beat, paid whether or not the feature is on. Import it inside the functions that use it. Task 2 adds a test that pins this.
- **`litellm.telemetry` defaults to `True`.** Set it to `False` (and `suppress_debug_info = True`) before any call.
- **The API key is never returned by any endpoint.** Reads expose `llm_api_key_set: bool` only. The audit entry records *that* the key changed, never its value or ciphertext.
- **Provider error text is never returned verbatim** — it can contain the request headers, key included. Everything passes through the scrubber from Task 2.
- **Backend tests only run on Linux** (`app` imports `fcntl`) and most need a reachable Postgres. Use the containerised runner below; never run `pytest` on the Windows host.
- **Run pytest WITHOUT `-q`** — `pyproject.toml` already sets it, and `-qq` hides the pass count.
- **`ruff format --check .` reports ~32 pre-existing unformatted files.** Only format files you create; never reformat a file you did not otherwise touch.
- **Line endings must be LF.** The Edit/Write tools here can emit CRLF and `git status` hides it. After editing run `git ls-files --eol <files>`; anything `w/crlf` gets `sed -i 's/\r$//'`.
- **Schema changes need two regenerations:** `docker exec megoopm-test python -m scripts.export_openapi`, then `cd frontend && npm run gen:api`.
- **vitest does not typecheck** — run `npm run typecheck` separately. Frontend commands run from `frontend/`.
- Commits go **directly to `main`**, the operator's established preference for this repo. Confirm before Task 1's commit if that has changed.

### Two deliberate deviations from the spec

1. **How tests substitute for litellm.** The spec's service sketch does not say. Because the import is lazy, tests inject a fake module into `sys.modules` before calling — which also gives the telemetry assertion something real to check (`fake.telemetry is False` after a call). Task 2 defines that fixture; later tasks reuse it.

2. **`load_config(db)` is not built here.** The spec lists it on the service, raising `LlmNotConfiguredError` when the feature is switched off. But the spec also establishes that the probe — this plan's only consumer — must deliberately *ignore* `llm_enabled`, so `load_config` would have no caller: a function whose entire purpose is a check nothing performs. What it would have done splits cleanly, and both halves are built — reading and decrypting the row is `llm_config_from_row` (Task 3), and `LlmNotConfiguredError` is raised by `complete` when there is no model (Task 2). The disabled check belongs with the first feature that must respect it, which is Part B.

### Running the backend tests

```bash
export MSYS_NO_PATHCONV=1
docker network create megoopm-testnet
docker run -d --name megoopm-testdb --network megoopm-testnet \
  -e POSTGRES_USER=megoopm -e POSTGRES_PASSWORD=megoopm -e POSTGRES_DB=megoopm postgres:16-alpine
docker run -d --name megoopm-test --network megoopm-testnet --user root \
  -v "C:/Projects/megoopm/backend:/src" -w /src \
  -e CELERY_TASK_ALWAYS_EAGER=true -e CELERY_RESULT_BACKEND=cache+memory:// \
  -e DATABASE_URL="postgresql+asyncpg://megoopm:megoopm@megoopm-testdb:5432/megoopm" \
  --entrypoint sleep megoopm-backend infinity
docker exec megoopm-test pip install -q "pytest>=8.2" "pytest-asyncio>=0.23" "aiosqlite>=0.20" "ruff>=0.6"
```

Per run: `docker exec megoopm-test python -m pytest tests/<file> -p no:cacheprovider -p no:warnings`

Teardown: `docker rm -f megoopm-test megoopm-testdb && docker network rm megoopm-testnet`

---

## File Structure

**Created:**

| file | responsibility |
| --- | --- |
| `backend/alembic/versions/0020_llm_settings.py` | four columns + the CHECK |
| `backend/app/services/llm.py` | the only module that touches litellm: config, completion, probe, scrubber |
| `backend/tests/test_llm_service.py` | scrubber, service, lazy-import guard |
| `frontend/src/components/settings/llm-card.tsx` | the LLM Integration card |
| `frontend/src/components/settings/llm-card.test.tsx` | its tests |

**Modified:**

| file | change |
| --- | --- |
| `backend/pyproject.toml` | `litellm>=1.99` |
| `backend/app/models/instance_settings.py` | four columns, one CHECK |
| `backend/app/schemas/instance_settings.py` | `llm_api_key_set` on read; `LlmSettingsUpdate`, `LlmTestRequest`, `LlmTestResult` |
| `backend/app/services/instance_settings.py` | `update_llm_settings`, `llm_config_from_row` |
| `backend/app/api/routes/settings.py` | split PATCHes + the probe |
| `backend/openapi.json` | regenerated |
| `backend/tests/test_settings_api.py` | the renamed route, LLM PATCH, probe |
| `frontend/src/lib/api/resources/settings.ts` | `updateDefaultSite`, `updateLlm`, `testLlm` |
| `frontend/src/components/settings/lib.ts` | LLM form state + validation |
| `frontend/src/components/settings/lib.test.ts` | its tests |
| `frontend/src/components/settings/settings-view.tsx` | mount the card; adopt the renamed call |
| `frontend/src/components/settings/settings-view.test.tsx` | follow the rename |
| `frontend/src/lib/api/generated/schema.ts` | regenerated |
| `docs/data-model.md` | the four columns |

---

### Task 1: Data model and migration

**Files:**
- Create: `backend/alembic/versions/0020_llm_settings.py`
- Modify: `backend/app/models/instance_settings.py`, `docs/data-model.md`
- Test: `backend/tests/test_settings_api.py` (extend)

**Interfaces:**
- Produces: `InstanceSettings.llm_enabled: bool`, `.llm_model: str | None`, `.llm_api_base: str | None`, `.llm_api_key_enc: str | None`. Migration revision `0020_llm_settings`, down-revision `0019_instance_settings`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_settings_api.py`:

```python
async def test_llm_is_off_on_a_fresh_instance(pg_conn) -> None:
    """Enabling by upgrade would make the proxy call a third party unasked."""
    result = await pg_conn.execute(
        text("SELECT llm_enabled, llm_model, llm_api_key_enc FROM instance_settings WHERE id = 1")
    )
    row = result.one()
    assert row.llm_enabled is False
    assert row.llm_model is None
    assert row.llm_api_key_enc is None


async def test_enabling_llm_without_a_model_is_rejected_by_the_database(pg_conn) -> None:
    """An enabled, modelless config is switched on and inert — worse than refused."""
    with pytest.raises(IntegrityError):
        await pg_conn.execute(
            text(
                "UPDATE instance_settings SET llm_enabled = true, llm_model = NULL "
                "WHERE id = 1"
            )
        )


async def test_a_key_may_be_absent_when_enabled(pg_conn) -> None:
    """Ollama, LM Studio and vLLM need no key; demanding one locks them out."""
    await pg_conn.execute(
        text(
            "UPDATE instance_settings SET llm_enabled = true, llm_model = 'ollama/llama3', "
            "llm_api_key_enc = NULL WHERE id = 1"
        )
    )
    result = await pg_conn.execute(text("SELECT llm_model FROM instance_settings WHERE id = 1"))
    assert result.scalar_one() == "ollama/llama3"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_settings_api.py -p no:cacheprovider -p no:warnings`
Expected: FAIL — `column "llm_enabled" does not exist`.

- [ ] **Step 3: Add the columns to the model**

In `backend/app/models/instance_settings.py`, add to `__table_args__` after the two existing constraints:

```python
        CheckConstraint(
            "llm_enabled = false OR llm_model IS NOT NULL",
            name="llm_needs_model",
        ),
```

and after `default_site_page_id`:

```python
    # --- LLM integration -----------------------------------------------
    # Off by default: this opens outbound connections from a reverse proxy's
    # admin backend to a third party, which must never start because an
    # upgrade shipped.
    llm_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # litellm's model string, which already encodes the provider —
    # "gpt-4o", "anthropic/claude-sonnet-4", "ollama/llama3".
    llm_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Only needed when the endpoint is not the provider's default: a local
    # runner, or a gateway.
    llm_api_base: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Fernet token (app.core.crypto), never plaintext. Nullable on purpose:
    # a local model legitimately needs no key.
    llm_api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Add `Boolean` to the `sqlalchemy` import line, which currently reads
`from sqlalchemy import BigInteger, CheckConstraint, Enum, ForeignKey, Integer, Text`.

- [ ] **Step 4: Write the migration**

Create `backend/alembic/versions/0020_llm_settings.py`:

```python
"""LLM integration settings on the instance-settings singleton

Four columns: whether the feature is on, litellm's model string, an optional
API base for local runners and gateways, and the Fernet-encrypted API key.

Seeded off. Enabling by migration would make a reverse proxy's admin backend
start calling a third party because an upgrade shipped.

Revision ID: 0020_llm_settings
Revises: 0019_instance_settings
Create Date: 2026-09-01 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020_llm_settings"
down_revision: str | None = "0019_instance_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "instance_settings",
        sa.Column("llm_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("instance_settings", sa.Column("llm_model", sa.Text(), nullable=True))
    op.add_column("instance_settings", sa.Column("llm_api_base", sa.Text(), nullable=True))
    op.add_column("instance_settings", sa.Column("llm_api_key_enc", sa.Text(), nullable=True))
    # Bare name: the ck_%(table_name)s_%(constraint_name)s convention is applied
    # by alembic, so an expanded name would be double-prefixed. No constraint
    # requires a key — a local model legitimately has none.
    op.create_check_constraint(
        "llm_needs_model",
        "instance_settings",
        "llm_enabled = false OR llm_model IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_instance_settings_llm_needs_model"), "instance_settings", type_="check"
    )
    op.drop_column("instance_settings", "llm_api_key_enc")
    op.drop_column("instance_settings", "llm_api_base")
    op.drop_column("instance_settings", "llm_model")
    op.drop_column("instance_settings", "llm_enabled")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker exec megoopm-test python -m pytest tests/test_settings_api.py -p no:cacheprovider -p no:warnings`
Expected: PASS.

- [ ] **Step 6: Verify the migration against a fresh database**

The suite builds tables with `create_all` and never runs migrations, so this is separate. Note this migration is `add_column` on an existing table, not `create_table` — so it must be checked against a database that already has `0019` applied, which upgrading from empty gives you.

```bash
export MSYS_NO_PATHCONV=1
docker run -d --name megoopm-migdb --network megoopm-testnet \
  -e POSTGRES_USER=megoopm -e POSTGRES_PASSWORD=megoopm -e POSTGRES_DB=megoopm postgres:16-alpine
docker run -d --name megoopm-mig --network megoopm-testnet --user root \
  -v "C:/Projects/megoopm/backend:/src" -w /src \
  -e DATABASE_URL="postgresql+asyncpg://megoopm:megoopm@megoopm-migdb:5432/megoopm" \
  --entrypoint sleep megoopm-backend infinity
docker exec megoopm-mig alembic upgrade head
docker exec megoopm-migdb psql -U megoopm -d megoopm -c "\d instance_settings"
docker exec megoopm-migdb psql -U megoopm -d megoopm -c "SELECT llm_enabled, llm_model FROM instance_settings"
docker exec megoopm-mig alembic downgrade -1 && docker exec megoopm-mig alembic upgrade head
docker rm -f megoopm-mig megoopm-migdb
```

Expected: the four columns and `ck_instance_settings_llm_needs_model` present; the seeded row reads `f | (null)`; the downgrade/re-upgrade round trip succeeds. **The downgrade must drop the CHECK before the columns** — dropping a column a constraint references fails.

- [ ] **Step 7: Document the columns**

In `docs/data-model.md`, extend the `instance_settings` entry to mention it now also holds the LLM integration config, and add to the Constraints list:

```markdown
- `instance_settings`: … and `llm_enabled = true` requires `llm_model`. No
  constraint requires an API key — a local model (Ollama, LM Studio, vLLM)
  legitimately has none.
```

- [ ] **Step 8: Lint, check line endings, commit**

```bash
docker exec megoopm-test ruff check app tests alembic
docker exec megoopm-test ruff format --check app/models/instance_settings.py alembic/versions/0020_llm_settings.py
git ls-files --eol backend/app/models/instance_settings.py backend/alembic/versions/0020_llm_settings.py backend/tests/test_settings_api.py docs/data-model.md
git add backend/app/models backend/alembic/versions/0020_llm_settings.py backend/tests/test_settings_api.py docs/data-model.md
git commit -m "feat(llm): add LLM integration columns to the settings singleton"
```

---

### Task 2: The LLM service and the scrubber

**Files:**
- Create: `backend/app/services/llm.py`, `backend/tests/test_llm_service.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Produces: `LlmConfig(model, api_key=None, api_base=None)`; `LlmCheckResult(ok, model, reply="", error="", latency_ms=0)`; `LlmError`; `scrub_secrets(text, *, key=None) -> str`; `async complete(config, *, prompt, system=None, max_tokens=None, timeout=60.0) -> str`; `async check_connection(config, *, timeout=30.0) -> LlmCheckResult`.
- Note: `complete` and `check_connection` take a config, never a session, so the whole surface is testable without a database.

This is the security-critical task. The scrubber is what stands between a provider's error text — which can contain the request headers, key included — and an admin's browser.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_llm_service.py`:

```python
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
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_llm_service.py -p no:cacheprovider -p no:warnings`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.llm'`.

- [ ] **Step 3: Write the service**

Create `backend/app/services/llm.py`:

```python
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


async def check_connection(config: LlmConfig, *, timeout: float = 30.0) -> LlmCheckResult:
    """Probe the configuration end to end and report, never raise.

    A minimal completion is the only thing that proves the *whole* path —
    credentials, base URL, model name, and the provider actually answering.
    """
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
    "check_connection",
    "complete",
    "scrub_secrets",
]
```

- [ ] **Step 4: Add the dependency**

In `backend/pyproject.toml`, append to `dependencies`:

```toml
    # LLM integration: one model string names any of ~100 providers. Heavy —
    # ~190 MB with openai/tokenizers/huggingface-hub — and 3.49s to import,
    # which is why app/services/llm.py imports it lazily.
    "litellm>=1.99",
```

- [ ] **Step 5: Run test to verify it passes**

Run: `docker exec megoopm-test python -m pytest tests/test_llm_service.py -p no:cacheprovider -p no:warnings`
Expected: PASS. The tests use the fake, so litellm need not be installed in the container yet.

- [ ] **Step 6: Rebuild the backend image so the real package is present**

The tests mock litellm, but the running app needs it. Rebuild, then recreate the test container from the new image so later tasks run against the real dependency set:

```bash
export MSYS_NO_PATHCONV=1
docker build -t megoopm-backend ./backend
docker rm -f megoopm-test
docker run -d --name megoopm-test --network megoopm-testnet --user root \
  -v "C:/Projects/megoopm/backend:/src" -w /src \
  -e CELERY_TASK_ALWAYS_EAGER=true -e CELERY_RESULT_BACKEND=cache+memory:// \
  -e DATABASE_URL="postgresql+asyncpg://megoopm:megoopm@megoopm-testdb:5432/megoopm" \
  --entrypoint sleep megoopm-backend infinity
docker exec megoopm-test pip install -q "pytest>=8.2" "pytest-asyncio>=0.23" "aiosqlite>=0.20" "ruff>=0.6"
docker exec megoopm-test python -c "import litellm; print(litellm.__version__)"
docker exec megoopm-test python -m pytest tests/test_llm_service.py -p no:cacheprovider -p no:warnings
```

Expected: a version prints, and the suite still passes — including
`test_litellm_is_not_imported_when_the_app_is`, which is only meaningful now
that litellm is actually installed and *could* be imported.

- [ ] **Step 7: Lint, check line endings, commit**

```bash
docker exec megoopm-test ruff check app tests
docker exec megoopm-test ruff format --check app/services/llm.py tests/test_llm_service.py
git ls-files --eol backend/app/services/llm.py backend/tests/test_llm_service.py backend/pyproject.toml
git add backend/app/services/llm.py backend/tests/test_llm_service.py backend/pyproject.toml
git commit -m "feat(llm): add the LLM client seam, lazily imported and scrubbed"
```

---

### Task 3: Schemas, service update, and the route split

**Files:**
- Modify: `backend/app/schemas/instance_settings.py`, `backend/app/services/instance_settings.py`, `backend/app/api/routes/settings.py`, `backend/openapi.json`
- Test: `backend/tests/test_settings_api.py` (extend)

**Interfaces:**
- Consumes: `InstanceSettings.llm_*` (Task 1), `LlmConfig` (Task 2).
- Produces: `InstanceSettingsRead.llm_enabled/llm_model/llm_api_base/llm_api_key_set`; `LlmSettingsUpdate(llm_enabled, llm_model=None, llm_api_base=None, llm_api_key=<unset|str|None>)`; `update_llm_settings(db, changes) -> InstanceSettings`; `llm_config_from_row(row) -> LlmConfig`; routes `PATCH /api/v1/settings/default-site` and `PATCH /api/v1/settings/llm`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_settings_api.py`. Note the first test pins the **rename**, so the existing default-site tests must be updated too — change every `client.patch("/api/v1/settings", ...)` in this file to `client.patch("/api/v1/settings/default-site", ...)`, including the one inside `_point_default_site_at` in `tests/test_custom_pages_api.py`.

```python
# --- LLM settings ----------------------------------------------------------

LLM_KEY = "sk-EXAMPLE-not-a-real-credential-1"


async def _enable_llm(client: AsyncClient, auth, **overrides) -> dict:
    body = {
        "llm_enabled": True,
        "llm_model": "gpt-4o",
        "llm_api_key": LLM_KEY,
    } | overrides
    resp = await client.patch("/api/v1/settings/llm", headers=auth, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_default_site_moved_to_its_own_path(client: AsyncClient, auth) -> None:
    """The bare PATCH is gone: one route per settings group."""
    assert (
        await client.patch(
            "/api/v1/settings", headers=auth, json={"default_site_mode": "not_found"}
        )
    ).status_code == 405


async def test_llm_settings_round_trip(client: AsyncClient, auth) -> None:
    body = await _enable_llm(client, auth, llm_api_base="https://gw.example.com")
    assert body["llm_enabled"] is True
    assert body["llm_model"] == "gpt-4o"
    assert body["llm_api_base"] == "https://gw.example.com"


async def test_the_key_is_never_returned(client: AsyncClient, auth) -> None:
    """A compromised browser session must not be able to read it back out."""
    resp = await client.patch(
        "/api/v1/settings/llm",
        headers=auth,
        json={"llm_enabled": True, "llm_model": "gpt-4o", "llm_api_key": LLM_KEY},
    )
    assert resp.status_code == 200, resp.text
    # Asserted against the raw body, not the parsed dict, so the key cannot hide
    # in a field nobody thought to check.
    assert LLM_KEY not in resp.text
    body = resp.json()
    assert body["llm_api_key_set"] is True
    assert "llm_api_key" not in body
    assert "llm_api_key_enc" not in body

    fetched = await client.get("/api/v1/settings", headers=auth)
    assert LLM_KEY not in fetched.text
    assert fetched.json()["llm_api_key_set"] is True


async def test_omitting_the_key_keeps_the_stored_one(client: AsyncClient, auth) -> None:
    """A client editing settings has no key to send back."""
    await _enable_llm(client, auth)
    resp = await client.patch(
        "/api/v1/settings/llm",
        headers=auth,
        json={"llm_enabled": True, "llm_model": "gpt-4o-mini"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["llm_api_key_set"] is True
    assert resp.json()["llm_model"] == "gpt-4o-mini"


async def test_an_explicit_null_clears_the_key(client: AsyncClient, auth) -> None:
    await _enable_llm(client, auth)
    resp = await client.patch(
        "/api/v1/settings/llm",
        headers=auth,
        json={"llm_enabled": True, "llm_model": "gpt-4o", "llm_api_key": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["llm_api_key_set"] is False


async def test_enabling_without_a_model_is_422(client: AsyncClient, auth) -> None:
    resp = await client.patch(
        "/api/v1/settings/llm", headers=auth, json={"llm_enabled": True}
    )
    assert resp.status_code == 422, resp.text


async def test_a_keyless_local_model_is_allowed(client: AsyncClient, auth) -> None:
    """Ollama and friends need no key; demanding one locks them out."""
    resp = await client.patch(
        "/api/v1/settings/llm",
        headers=auth,
        json={
            "llm_enabled": True,
            "llm_model": "ollama/llama3",
            "llm_api_base": "http://localhost:11434",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["llm_api_key_set"] is False


async def test_the_audit_entry_carries_no_key_material(client: AsyncClient, auth) -> None:
    await _enable_llm(client, auth)
    entries = await client.get("/api/v1/audit-log", headers=auth)
    assert entries.status_code == 200, entries.text
    assert LLM_KEY not in entries.text


async def test_llm_writes_do_not_touch_nginx(client: AsyncClient, auth, monkeypatch) -> None:
    """No rendered configuration references any of this."""
    calls = 0

    def _counting_reload() -> TaskEnqueued:
        nonlocal calls
        calls += 1
        return TaskEnqueued(task_id="test-reload-task", status="PENDING")

    monkeypatch.setattr(config_writes, "enqueue_nginx_reload", _counting_reload)
    await _enable_llm(client, auth)
    assert calls == 0


async def test_llm_endpoints_require_authentication(client: AsyncClient) -> None:
    assert (
        await client.patch("/api/v1/settings/llm", json={"llm_enabled": False})
    ).status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_settings_api.py -p no:cacheprovider -p no:warnings`
Expected: FAIL — the new LLM routes 404, and the renamed default-site route 404s until Step 5.

- [ ] **Step 3: Add the schemas**

In `backend/app/schemas/instance_settings.py`, add `llm_*` to the read model and the new write models. `InstanceSettingsRead` gains:

```python
    llm_enabled: bool
    llm_model: str | None
    llm_api_base: str | None
    llm_api_key_set: bool
```

`llm_api_key_set` is not a column, so the route builds the read model with
`InstanceSettingsRead.model_validate({..., "llm_api_key_set": row.llm_api_key_enc is not None})`.
Give it a helper on the schema instead, so no caller can forget:

```python
    @classmethod
    def from_row(cls, row: object) -> InstanceSettingsRead:
        """Build from an ORM row, deriving `llm_api_key_set` without exposing the key.

        A classmethod rather than a computed field so there is exactly one way
        to build this, and no path where a caller hands over the raw row and
        the ciphertext leaks into the response.
        """
        return cls(
            default_site_mode=row.default_site_mode,  # type: ignore[attr-defined]
            default_site_redirect_url=row.default_site_redirect_url,  # type: ignore[attr-defined]
            default_site_page_id=row.default_site_page_id,  # type: ignore[attr-defined]
            llm_enabled=row.llm_enabled,  # type: ignore[attr-defined]
            llm_model=row.llm_model,  # type: ignore[attr-defined]
            llm_api_base=row.llm_api_base,  # type: ignore[attr-defined]
            llm_api_key_set=row.llm_api_key_enc is not None,  # type: ignore[attr-defined]
            updated_at=row.updated_at,  # type: ignore[attr-defined]
        )
```

Then the update model. The `llm_api_key` field needs three states — absent, a
string, and explicit `null` — which Pydantic distinguishes through
`model_fields_set`:

```python
class LlmSettingsUpdate(BaseModel):
    """Set the LLM integration. Carries the whole group; the key is the exception.

    ``llm_enabled`` is required for the same reason ``default_site_mode`` is on
    its sibling: "enabled needs a model" cannot be checked against a payload
    that omits it, and a schema never sees the stored row.

    ``llm_api_key`` is the one field that cannot work that way — it is never
    returned, so a client has nothing to send back. Absent keeps the stored
    key; a string replaces it; an explicit ``null`` clears it. The three states
    are distinguished with ``model_fields_set``, which is why the service takes
    ``model_dump(exclude_unset=True)``.
    """

    llm_enabled: bool
    llm_model: str | None = None
    llm_api_base: str | None = None
    llm_api_key: str | None = None

    @field_validator("llm_model", "llm_api_base", "llm_api_key")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        """An empty input box means "not set", not "the empty string"."""
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _enabled_needs_a_model(self) -> LlmSettingsUpdate:
        if self.llm_enabled and not self.llm_model:
            raise ValueError("llm_model is required when llm_enabled is true")
        return self


class LlmTestRequest(BaseModel):
    """Optional overrides for the probe, so a key can be checked before saving."""

    model: str | None = None
    api_base: str | None = None
    api_key: str | None = None


class LlmTestResult(BaseModel):
    """The probe's outcome. `ok: false` still returns HTTP 200 — see the route."""

    ok: bool
    model: str
    reply: str = ""
    error: str = ""
    latency_ms: int = 0
```

Add all four names to `__all__`.

- [ ] **Step 4: Add the service functions**

In `backend/app/services/instance_settings.py`, add the import
`from app.core.crypto import decrypt_secret, encrypt_secret` and
`from app.services.llm import LlmConfig`, then:

```python
async def update_llm_settings(db: AsyncSession, changes: dict[str, Any]) -> InstanceSettings:
    """Apply an LLM settings payload, encrypting the key on the way in.

    ``changes`` must come from ``model_dump(exclude_unset=True)``: the presence
    or absence of ``llm_api_key`` is the signal for keep-vs-replace-vs-clear,
    and a plain dump would flatten "absent" into ``None`` and silently wipe a
    working key on every save.
    """
    row = await get_instance_settings(db)

    row.llm_enabled = changes["llm_enabled"]
    row.llm_model = changes.get("llm_model")
    row.llm_api_base = changes.get("llm_api_base")

    if "llm_api_key" in changes:
        key = changes["llm_api_key"]
        row.llm_api_key_enc = encrypt_secret(key) if key else None

    await db.commit()
    await db.refresh(row)
    return row


def llm_config_from_row(row: InstanceSettings) -> LlmConfig:
    """Decrypt the stored key into a config the LLM service can use."""
    return LlmConfig(
        model=row.llm_model or "",
        api_key=decrypt_secret(row.llm_api_key_enc) if row.llm_api_key_enc else None,
        api_base=row.llm_api_base,
    )
```

Add both to `__all__`.

- [ ] **Step 5: Split the routes**

In `backend/app/api/routes/settings.py`, update the module docstring's second
paragraph to:

```python
"""...

Settings are grouped, and each group gets its own ``PATCH``. A single patch over
the whole row cannot work: each group has a coherence rule ("redirect needs a
URL", "enabled needs a model") that can only be checked against a payload
carrying that group's discriminator, so every group would have to be resent to
change any one of them.

Only the default-site group renders into nginx, so only its write goes through
:func:`~app.api.routes._config_writes.after_config_write`. The LLM group is
audited with :func:`~app.services.audit.record_audit` and enqueues no reload.
"""
```

Change the default-site route's decorator from `@router.patch("")` to
`@router.patch("/default-site")` and leave its body as it is. Then add:

```python
@router.patch("/llm", response_model=InstanceSettingsRead)
async def update_llm_settings(
    body: LlmSettingsUpdate, admin: AdminUser, db: SessionDep
) -> InstanceSettingsRead:
    """Configure the LLM integration. Admin-only.

    ``exclude_unset`` is load-bearing: it is what tells the service the
    difference between "the client did not send a key" and "the client cleared
    the key".
    """
    changes = body.model_dump(exclude_unset=True)
    row = await settings_service.update_llm_settings(db, changes)
    await record_audit(
        db,
        actor=admin.email,
        action=AuditAction.update,
        object_type="instance_settings",
        object_id=row.id,
        # The field name, never the value — and never the ciphertext.
        meta={
            "llm_enabled": row.llm_enabled,
            "llm_model": row.llm_model,
            "llm_api_key_changed": "llm_api_key" in changes,
        },
    )
    await db.commit()
    return InstanceSettingsRead.from_row(row)
```

Import `record_audit` from `app.services.audit` and the new schemas. Replace
every `InstanceSettingsRead.model_validate(row)` in this module with
`InstanceSettingsRead.from_row(row)`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `docker exec megoopm-test python -m pytest tests/test_settings_api.py tests/test_custom_pages_api.py -p no:cacheprovider -p no:warnings`
Expected: PASS. If `test_default_site_moved_to_its_own_path` sees 404 rather
than 405, FastAPI has no route at `/settings` for any method — that is fine and
the assertion should be relaxed to `in (404, 405)`; a bare `PATCH` no longer
existing is the actual requirement.

- [ ] **Step 7: Regenerate OpenAPI, run the full suite, commit**

```bash
export MSYS_NO_PATHCONV=1
docker exec megoopm-test python -m scripts.export_openapi
docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings
docker exec megoopm-test ruff check app tests
docker exec megoopm-test ruff format --check app/schemas/instance_settings.py app/services/instance_settings.py app/api/routes/settings.py
git ls-files --eol backend/app/schemas/instance_settings.py backend/app/services/instance_settings.py backend/app/api/routes/settings.py backend/openapi.json backend/tests/test_settings_api.py backend/tests/test_custom_pages_api.py
git add backend/app backend/openapi.json backend/tests
git commit -m "feat(llm): store LLM settings, one PATCH per settings group"
```

---

### Task 4: The probe endpoint

**Files:**
- Modify: `backend/app/api/routes/settings.py`
- Test: `backend/tests/test_settings_api.py` (extend)

**Interfaces:**
- Consumes: `check_connection`, `LlmConfig` (Task 2); `llm_config_from_row` (Task 3); `LlmTestRequest`, `LlmTestResult` (Task 3).
- Produces: `POST /api/v1/settings/llm/test`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_settings_api.py`:

```python
# --- The probe -------------------------------------------------------------


@pytest.fixture
def stub_probe(monkeypatch):
    """Replace the LLM round trip; these tests are about the route, not litellm."""
    import app.api.routes.settings as settings_routes
    from app.services.llm import LlmCheckResult

    seen: list = []

    async def _check(config, *, timeout=30.0):
        seen.append(config)
        return LlmCheckResult(ok=True, model=config.model, reply="OK", latency_ms=7)

    monkeypatch.setattr(settings_routes, "check_connection", _check)
    return seen


async def test_probe_uses_the_stored_config(client: AsyncClient, auth, stub_probe) -> None:
    await _enable_llm(client, auth, llm_api_base="https://gw.example.com")
    resp = await client.post("/api/v1/settings/llm/test", headers=auth, json={})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "ok": True,
        "model": "gpt-4o",
        "reply": "OK",
        "error": "",
        "latency_ms": 7,
    }
    assert stub_probe[0].model == "gpt-4o"
    assert stub_probe[0].api_key == LLM_KEY
    assert stub_probe[0].api_base == "https://gw.example.com"


async def test_probe_accepts_overrides_so_a_key_can_be_checked_before_saving(
    client: AsyncClient, auth, stub_probe
) -> None:
    await _enable_llm(client, auth)
    resp = await client.post(
        "/api/v1/settings/llm/test",
        headers=auth,
        json={"model": "gpt-4o-mini", "api_key": "sk-unsaved-value-abcdefghijkl"},
    )
    assert resp.status_code == 200, resp.text
    assert stub_probe[0].model == "gpt-4o-mini"
    assert stub_probe[0].api_key == "sk-unsaved-value-abcdefghijkl"


async def test_probe_works_while_the_feature_is_switched_off(
    client: AsyncClient, auth, stub_probe
) -> None:
    """Configure, prove it works, then switch on — not the other way round."""
    await client.patch(
        "/api/v1/settings/llm",
        headers=auth,
        json={"llm_enabled": False, "llm_model": "gpt-4o", "llm_api_key": LLM_KEY},
    )
    resp = await client.post("/api/v1/settings/llm/test", headers=auth, json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


async def test_probe_without_a_model_is_422(client: AsyncClient, auth) -> None:
    """With no model there is nothing to probe."""
    resp = await client.post("/api/v1/settings/llm/test", headers=auth, json={})
    assert resp.status_code == 422, resp.text


async def test_a_failed_probe_is_200_with_ok_false(client: AsyncClient, auth, monkeypatch) -> None:
    """The API call succeeded; the upstream did not. An error status would make
    a working endpoint indistinguishable from a broken one in monitoring."""
    import app.api.routes.settings as settings_routes
    from app.services.llm import LlmCheckResult

    async def _check(config, *, timeout=30.0):
        return LlmCheckResult(ok=False, model=config.model, error="401 unauthorized")

    monkeypatch.setattr(settings_routes, "check_connection", _check)

    await _enable_llm(client, auth)
    resp = await client.post("/api/v1/settings/llm/test", headers=auth, json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is False
    assert resp.json()["error"] == "401 unauthorized"


async def test_probe_requires_authentication(client: AsyncClient) -> None:
    assert (await client.post("/api/v1/settings/llm/test", json={})).status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_settings_api.py -k probe -p no:cacheprovider -p no:warnings`
Expected: FAIL — the route 404s.

- [ ] **Step 3: Add the route**

In `backend/app/api/routes/settings.py`, import
`from app.services.llm import LlmConfig, check_connection` — a module-level
import of the *service*, which is safe: the service does not import litellm at
module scope, so this costs nothing at startup. Then:

```python
@router.post("/llm/test", response_model=LlmTestResult)
async def test_llm_connection(
    body: LlmTestRequest, _admin: AdminUser, db: SessionDep
) -> LlmTestResult:
    """Probe the LLM configuration end to end. Admin-only.

    Overrides in the body win over the stored row, so a key can be checked
    before it is saved.

    This deliberately ignores ``llm_enabled``. That flag stops *feature* code
    running when the operator has switched the integration off; requiring it
    here would invert the order an operator actually works in — configure,
    prove it works, then enable.

    A failed probe returns **200 with ``ok: false``**, not a 4xx or 5xx: the API
    call succeeded, the upstream did not.
    """
    row = await settings_service.get_instance_settings(db)
    stored = settings_service.llm_config_from_row(row)
    config = LlmConfig(
        model=body.model or stored.model,
        api_key=body.api_key or stored.api_key,
        api_base=body.api_base or stored.api_base,
    )
    if not config.model:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Set a model before testing the connection",
        )
    result = await check_connection(config)
    return LlmTestResult(
        ok=result.ok,
        model=result.model,
        reply=result.reply,
        error=result.error,
        latency_ms=result.latency_ms,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker exec megoopm-test python -m pytest tests/test_settings_api.py -p no:cacheprovider -p no:warnings`
Expected: PASS.

- [ ] **Step 5: Regenerate OpenAPI, lint, commit**

```bash
export MSYS_NO_PATHCONV=1
docker exec megoopm-test python -m scripts.export_openapi
docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings
docker exec megoopm-test ruff check app tests
git ls-files --eol backend/app/api/routes/settings.py backend/openapi.json backend/tests/test_settings_api.py
git add backend/app/api/routes/settings.py backend/openapi.json backend/tests/test_settings_api.py
git commit -m "feat(llm): probe the LLM configuration from the API"
```

---

### Task 5: Frontend client and form helpers

**Files:**
- Modify: `frontend/src/lib/api/resources/settings.ts`, `frontend/src/lib/api/index.ts`, `frontend/src/components/settings/lib.ts`, `frontend/src/components/settings/lib.test.ts`, `frontend/src/lib/api/generated/schema.ts`

**Interfaces:**
- Produces: `instanceSettings.updateDefaultSite(body)`, `.updateLlm(body)`, `.testLlm(body)`; types `LlmSettingsUpdate`, `LlmTestRequest`, `LlmTestResult`; `LlmFormState`, `emptyLlmState`, `llmStateFromSettings`, `validateLlmForm`, `buildLlmPayload`, `buildLlmTestPayload`.

- [ ] **Step 1: Regenerate the API types**

```bash
cd frontend && npm run gen:api
git -C /c/Projects/megoopm diff --stat src/lib/api/generated/schema.ts
```
Expected: `LlmSettingsUpdate`, `LlmTestRequest`, `LlmTestResult` appear and
`InstanceSettingsRead` grows the `llm_*` fields.

- [ ] **Step 2: Write the failing test**

Append to `frontend/src/components/settings/lib.test.ts`:

```typescript
import {
  buildLlmPayload,
  buildLlmTestPayload,
  emptyLlmState,
  llmStateFromSettings,
  validateLlmForm,
  type LlmFormState,
} from "@/components/settings/lib";

const LLM_SETTINGS: InstanceSettings = {
  ...SETTINGS,
  llm_enabled: true,
  llm_model: "gpt-4o",
  llm_api_base: "https://gw.example.com",
  llm_api_key_set: true,
};

function llm(overrides: Partial<LlmFormState> = {}): LlmFormState {
  return { ...emptyLlmState(), ...overrides };
}

describe("llmStateFromSettings", () => {
  it("seeds from the server row without a key it can never read", () => {
    expect(llmStateFromSettings(LLM_SETTINGS)).toEqual({
      enabled: true,
      model: "gpt-4o",
      apiBase: "https://gw.example.com",
      apiKey: "",
      keyIsSet: true,
    });
  });

  it("turns nulls into empty strings so the inputs stay controlled", () => {
    const seeded = llmStateFromSettings({
      ...LLM_SETTINGS,
      llm_model: null,
      llm_api_base: null,
      llm_api_key_set: false,
    });
    expect(seeded.model).toBe("");
    expect(seeded.apiBase).toBe("");
    expect(seeded.keyIsSet).toBe(false);
  });
});

describe("validateLlmForm", () => {
  it("passes while disabled, whatever else is blank", () => {
    expect(validateLlmForm(llm({ enabled: false }))).toBeNull();
  });

  it("requires a model to enable", () => {
    expect(validateLlmForm(llm({ enabled: true, model: "  " }))).toBe(
      "Enter a model to enable LLM features.",
    );
  });

  it("does not require a key — local models have none", () => {
    expect(validateLlmForm(llm({ enabled: true, model: "ollama/llama3" }))).toBeNull();
  });
});

describe("buildLlmPayload", () => {
  it("omits the key entirely when it was not retyped", () => {
    const payload = buildLlmPayload(
      llm({ enabled: true, model: "gpt-4o", apiKey: "", keyIsSet: true }),
    );
    expect("llm_api_key" in payload).toBe(false);
    expect(payload).toEqual({
      llm_enabled: true,
      llm_model: "gpt-4o",
      llm_api_base: null,
    });
  });

  it("sends a retyped key", () => {
    const payload = buildLlmPayload(
      llm({ enabled: true, model: "gpt-4o", apiKey: "  sk-new  ", keyIsSet: true }),
    );
    expect(payload.llm_api_key).toBe("sk-new");
  });

  it("sends an explicit null when the key is cleared", () => {
    const payload = buildLlmPayload(
      llm({ enabled: true, model: "gpt-4o", apiKey: "", keyIsSet: false, keyCleared: true }),
    );
    expect(payload.llm_api_key).toBeNull();
  });
});

describe("buildLlmTestPayload", () => {
  it("sends only what the form actually holds, so stored values fill the rest", () => {
    expect(
      buildLlmTestPayload(llm({ model: "gpt-4o", apiBase: "", apiKey: "" })),
    ).toEqual({ model: "gpt-4o" });
  });

  it("includes a typed key so it can be checked before saving", () => {
    expect(
      buildLlmTestPayload(llm({ model: "gpt-4o", apiKey: "sk-typed" })),
    ).toEqual({ model: "gpt-4o", api_key: "sk-typed" });
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/settings/lib.test.ts`
Expected: FAIL — the new exports do not exist.

- [ ] **Step 4: Update the API resource**

Replace `frontend/src/lib/api/resources/settings.ts`:

```typescript
/**
 * Typed client for the instance-settings endpoints.
 *
 * One settings row exists, so no path carries an id — but each settings *group*
 * gets its own PATCH. A single patch over the whole row cannot work: each group
 * has a coherence rule ("redirect needs a URL", "enabled needs a model") that
 * can only be checked against a payload carrying that group's discriminator, so
 * one combined route would force resending every group to change any of them.
 *
 * The LLM API key is never returned by `get` — only `llm_api_key_set`.
 */
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";

export type InstanceSettings = Schemas["InstanceSettingsRead"];
export type DefaultSiteUpdate = Schemas["InstanceSettingsUpdate"];
export type DefaultSiteMode = Schemas["DefaultSiteMode"];
export type LlmSettingsUpdate = Schemas["LlmSettingsUpdate"];
export type LlmTestRequest = Schemas["LlmTestRequest"];
export type LlmTestResult = Schemas["LlmTestResult"];

const BASE = "/api/v1/settings";

export const instanceSettings = {
  get: () => api.get<InstanceSettings>(BASE),
  updateDefaultSite: (body: DefaultSiteUpdate) =>
    api.patch<InstanceSettings>(`${BASE}/default-site`, body),
  updateLlm: (body: LlmSettingsUpdate) => api.patch<InstanceSettings>(`${BASE}/llm`, body),
  /** Runs a real completion. Overrides win over stored values, so a key can be
   *  checked before it is saved. A failed probe is `ok: false`, not an error. */
  testLlm: (body: LlmTestRequest) => api.post<LlmTestResult>(`${BASE}/llm/test`, body),
} as const;
```

In `frontend/src/lib/api/index.ts`, replace the `InstanceSettingsUpdate` type
export with `DefaultSiteUpdate`, `LlmSettingsUpdate`, `LlmTestRequest` and
`LlmTestResult`.

- [ ] **Step 5: Add the form helpers**

Append to `frontend/src/components/settings/lib.ts`:

```typescript
/* -------------------------------------------------------------------------- */
/* LLM integration                                                             */
/* -------------------------------------------------------------------------- */

/**
 * The key is the awkward field: it is never returned, so the form cannot show
 * it. `keyIsSet` is what the server says is stored; `apiKey` is what the
 * operator has typed *now*; `keyCleared` records an explicit "remove it",
 * which is the only way to distinguish clearing from simply not retyping.
 */
export type LlmFormState = {
  enabled: boolean;
  model: string;
  apiBase: string;
  apiKey: string;
  keyIsSet: boolean;
  keyCleared?: boolean;
};

export function emptyLlmState(): LlmFormState {
  return { enabled: false, model: "", apiBase: "", apiKey: "", keyIsSet: false };
}

export function llmStateFromSettings(settings: InstanceSettings): LlmFormState {
  return {
    enabled: settings.llm_enabled,
    model: settings.llm_model ?? "",
    apiBase: settings.llm_api_base ?? "",
    // Never prefilled — the API does not return it.
    apiKey: "",
    keyIsSet: settings.llm_api_key_set,
  };
}

/** The first problem blocking a save, or `null` when the form is ready. */
export function validateLlmForm(state: LlmFormState): string | null {
  if (!state.enabled) return null;
  if (!state.model.trim()) return "Enter a model to enable LLM features.";
  // Deliberately no key check: Ollama, LM Studio and vLLM need none.
  return null;
}

export function buildLlmPayload(state: LlmFormState): LlmSettingsUpdate {
  const payload: LlmSettingsUpdate = {
    llm_enabled: state.enabled,
    llm_model: state.model.trim() || null,
    llm_api_base: state.apiBase.trim() || null,
  };
  // Three states, and the difference matters: omitted keeps the stored key,
  // a string replaces it, an explicit null clears it. Sending "" on every save
  // would wipe a working key the operator never touched.
  if (state.apiKey.trim()) {
    payload.llm_api_key = state.apiKey.trim();
  } else if (state.keyCleared) {
    payload.llm_api_key = null;
  }
  return payload;
}

/** Only what the form holds; the server fills the rest from the stored row. */
export function buildLlmTestPayload(state: LlmFormState): LlmTestRequest {
  const payload: LlmTestRequest = {};
  if (state.model.trim()) payload.model = state.model.trim();
  if (state.apiBase.trim()) payload.api_base = state.apiBase.trim();
  if (state.apiKey.trim()) payload.api_key = state.apiKey.trim();
  return payload;
}
```

Extend the module's import line to
`import type { DefaultSiteMode, InstanceSettings, InstanceSettingsUpdate, LlmSettingsUpdate, LlmTestRequest } from "@/lib/api";`
— renaming `InstanceSettingsUpdate` to `DefaultSiteUpdate` throughout, including
in `buildDefaultSitePayload`'s return type.

- [ ] **Step 6: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/settings/lib.test.ts`
Expected: PASS.

- [ ] **Step 7: Typecheck, lint, commit**

```bash
cd frontend && npm run typecheck && npm run lint
git ls-files --eol frontend/src/lib/api/resources/settings.ts frontend/src/lib/api/index.ts frontend/src/components/settings/lib.ts frontend/src/components/settings/lib.test.ts
git add frontend/src/lib/api frontend/src/components/settings
git commit -m "feat(llm): typed client and form helpers for LLM settings"
```

---

### Task 6: The LLM Integration card

**Files:**
- Create: `frontend/src/components/settings/llm-card.tsx`, `frontend/src/components/settings/llm-card.test.tsx`
- Modify: `frontend/src/components/settings/settings-view.tsx`, `frontend/src/components/settings/settings-view.test.tsx`

**Interfaces:**
- Consumes: everything from Task 5.
- Produces: `<LlmCard settings={InstanceSettings} onSaved={(s: InstanceSettings) => void} />`.

The card owns its own state and save. `SettingsView` already loads the settings
row and passes it down, so the two cards stay independent — a reviewer can
reject one without touching the other.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/settings/llm-card.test.tsx`:

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { instanceSettings, type InstanceSettings } from "@/lib/api";
import { LlmCard } from "@/components/settings/llm-card";

function makeSettings(overrides: Partial<InstanceSettings> = {}): InstanceSettings {
  return {
    default_site_mode: "not_found",
    default_site_redirect_url: null,
    default_site_page_id: null,
    llm_enabled: false,
    llm_model: null,
    llm_api_base: null,
    llm_api_key_set: false,
    updated_at: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

function renderCard(settings = makeSettings()) {
  return render(<LlmCard settings={settings} onSaved={() => {}} />);
}

describe("LlmCard", () => {
  beforeEach(() => {
    vi.spyOn(instanceSettings, "updateLlm").mockResolvedValue(makeSettings());
    vi.spyOn(instanceSettings, "testLlm").mockResolvedValue({
      ok: true,
      model: "gpt-4o",
      reply: "OK",
      error: "",
      latency_ms: 412,
    });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("starts disabled on a fresh instance", () => {
    renderCard();
    expect(screen.getByLabelText("Enable LLM features")).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("never prefills the key, and says whether one is stored", () => {
    renderCard(makeSettings({ llm_api_key_set: true, llm_model: "gpt-4o" }));
    const key = screen.getByLabelText("API key");
    expect(key).toHaveValue("");
    expect(screen.getByText(/a key is stored/i)).toBeInTheDocument();
  });

  it("says when no key is stored", () => {
    renderCard();
    expect(screen.getByText(/no key stored/i)).toBeInTheDocument();
  });

  it("saves the group without a key when the key was not retyped", async () => {
    const user = userEvent.setup();
    renderCard(makeSettings({ llm_enabled: true, llm_model: "gpt-4o", llm_api_key_set: true }));

    await user.clear(screen.getByLabelText("Model"));
    await user.type(screen.getByLabelText("Model"), "gpt-4o-mini");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(instanceSettings.updateLlm).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(instanceSettings.updateLlm).mock.calls[0][0];
    expect(payload.llm_model).toBe("gpt-4o-mini");
    expect("llm_api_key" in payload).toBe(false);
  });

  it("sends a retyped key", async () => {
    const user = userEvent.setup();
    renderCard(makeSettings({ llm_enabled: true, llm_model: "gpt-4o" }));

    await user.type(screen.getByLabelText("API key"), "sk-brand-new");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(instanceSettings.updateLlm).toHaveBeenCalledTimes(1));
    expect(vi.mocked(instanceSettings.updateLlm).mock.calls[0][0].llm_api_key).toBe(
      "sk-brand-new",
    );
  });

  it("clears a stored key on demand", async () => {
    const user = userEvent.setup();
    renderCard(makeSettings({ llm_enabled: true, llm_model: "gpt-4o", llm_api_key_set: true }));

    await user.click(screen.getByRole("button", { name: "Remove stored key" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(instanceSettings.updateLlm).toHaveBeenCalledTimes(1));
    expect(vi.mocked(instanceSettings.updateLlm).mock.calls[0][0].llm_api_key).toBeNull();
  });

  it("blocks enabling with no model and says why", async () => {
    const user = userEvent.setup();
    renderCard();
    await user.click(screen.getByLabelText("Enable LLM features"));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter a model to enable LLM features.",
    );
    expect(instanceSettings.updateLlm).not.toHaveBeenCalled();
  });

  it("shows the reply and round trip on a successful probe", async () => {
    const user = userEvent.setup();
    renderCard(makeSettings({ llm_model: "gpt-4o" }));

    await user.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText(/OK/)).toBeInTheDocument();
    expect(screen.getByText(/412\s*ms/)).toBeInTheDocument();
  });

  it("shows the error when the probe fails, without treating it as a crash", async () => {
    vi.mocked(instanceSettings.testLlm).mockResolvedValue({
      ok: false,
      model: "gpt-4o",
      reply: "",
      error: "401 unauthorized",
      latency_ms: 88,
    });
    const user = userEvent.setup();
    renderCard(makeSettings({ llm_model: "gpt-4o" }));

    await user.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("401 unauthorized");
  });

  it("tests what is in the form, so a key can be checked before saving", async () => {
    const user = userEvent.setup();
    renderCard(makeSettings({ llm_model: "gpt-4o" }));

    await user.type(screen.getByLabelText("API key"), "sk-unsaved");
    await user.click(screen.getByRole("button", { name: "Test connection" }));

    await waitFor(() => expect(instanceSettings.testLlm).toHaveBeenCalledTimes(1));
    expect(vi.mocked(instanceSettings.testLlm).mock.calls[0][0]).toEqual({
      model: "gpt-4o",
      api_key: "sk-unsaved",
    });
  });

  it("can test while the feature is switched off", async () => {
    const user = userEvent.setup();
    renderCard(makeSettings({ llm_enabled: false, llm_model: "gpt-4o" }));
    expect(screen.getByRole("button", { name: "Test connection" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Test connection" }));
    await waitFor(() => expect(instanceSettings.testLlm).toHaveBeenCalledTimes(1));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/settings/llm-card.test.tsx`
Expected: FAIL — `Cannot find module '@/components/settings/llm-card'`.

- [ ] **Step 3: Write the card**

Create `frontend/src/components/settings/llm-card.tsx`:

```tsx
"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import { instanceSettings, type InstanceSettings, type LlmTestResult } from "@/lib/api";
import {
  buildLlmPayload,
  buildLlmTestPayload,
  describeError,
  llmStateFromSettings,
  validateLlmForm,
  type LlmFormState,
} from "@/components/settings/lib";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

/**
 * Configure the LLM integration.
 *
 * Owns its own state and save so it stays independent of the Default site card
 * beside it. The API key is the awkward part: it is never returned, so the
 * field starts empty and shows whether one is stored rather than what it is.
 */
export function LlmCard({
  settings,
  onSaved,
}: {
  settings: InstanceSettings;
  onSaved: (settings: InstanceSettings) => void;
}) {
  const [form, setForm] = useState<LlmFormState>(() => llmStateFromSettings(settings));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<LlmTestResult | null>(null);

  function patch(changes: Partial<LlmFormState>) {
    setForm((current) => ({ ...current, ...changes }));
  }

  async function handleSave() {
    const problem = validateLlmForm(form);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const updated = await instanceSettings.updateLlm(buildLlmPayload(form));
      setForm(llmStateFromSettings(updated));
      toast.success("LLM settings saved");
      onSaved(updated);
    } catch (err) {
      const described = describeError(err);
      setError(described.message);
      toast.error(described.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setError(null);
    setResult(null);
    setTesting(true);
    try {
      // Sends what is in the form, so an unsaved key can be checked. The server
      // fills anything omitted from the stored row.
      setResult(await instanceSettings.testLlm(buildLlmTestPayload(form)));
    } catch (err) {
      // A failed *probe* comes back as ok:false with 200; reaching here means
      // the request itself failed.
      setError(describeError(err).message);
    } finally {
      setTesting(false);
    }
  }

  return (
    <section className="space-y-4 rounded-xl border p-5">
      <div>
        <h3 className="text-sm font-semibold">LLM Integration</h3>
        <p className="text-sm text-muted-foreground">
          Let MegooPM call a language model. Off by default — this opens
          outbound connections from your proxy to a third party.
        </p>
      </div>

      <label className="flex items-start gap-2">
        <Switch
          aria-label="Enable LLM features"
          checked={form.enabled}
          onCheckedChange={(enabled) => patch({ enabled })}
          disabled={saving}
        />
        <span className="space-y-0.5">
          <span className="block text-sm font-medium leading-none">
            Enable LLM features
          </span>
          <span className="block text-xs text-muted-foreground">
            Features that call the model stay inert until this is on.
          </span>
        </span>
      </label>

      <div className="space-y-1.5">
        <Label htmlFor="llm-model">Model</Label>
        <Input
          id="llm-model"
          value={form.model}
          onChange={(e) => patch({ model: e.target.value })}
          placeholder="gpt-4o"
          disabled={saving}
        />
        <p className="text-xs text-muted-foreground">
          The provider is part of the name — <code>anthropic/claude-sonnet-4</code>,{" "}
          <code>ollama/llama3</code>.
        </p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="llm-key">API key</Label>
        <div className="flex gap-2">
          <Input
            id="llm-key"
            type="password"
            value={form.apiKey}
            onChange={(e) => patch({ apiKey: e.target.value, keyCleared: false })}
            placeholder={form.keyIsSet ? "leave blank to keep" : "sk-…"}
            disabled={saving}
            className="flex-1"
          />
          {form.keyIsSet ? (
            <Button
              variant="outline"
              size="sm"
              disabled={saving}
              onClick={() => patch({ apiKey: "", keyIsSet: false, keyCleared: true })}
            >
              Remove stored key
            </Button>
          ) : null}
        </div>
        <p className="text-xs text-muted-foreground">
          {form.keyIsSet ? "A key is stored." : "No key stored."} Leave blank for a
          local model that needs none — in that case the provider library may
          fall back to an API key set in the environment.
        </p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="llm-base">API base</Label>
        <Input
          id="llm-base"
          value={form.apiBase}
          onChange={(e) => patch({ apiBase: e.target.value })}
          placeholder="optional — e.g. http://localhost:11434"
          disabled={saving}
        />
      </div>

      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}

      {result ? (
        result.ok ? (
          <p className="rounded-lg border border-success/30 bg-success/5 p-3 text-sm">
            <span className="font-medium">Connected.</span> {result.model} replied{" "}
            <span className="font-mono">{result.reply}</span> in {result.latency_ms} ms.
          </p>
        ) : (
          <p role="alert" className="text-sm text-destructive">
            {result.error}
          </p>
        )
      ) : null}

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={handleTest} disabled={testing || saving}>
          {testing ? <Loader2 className="animate-spin" /> : null}
          Test connection
        </Button>
        <Button onClick={handleSave} disabled={saving || testing}>
          {saving ? "Saving…" : "Save changes"}
        </Button>
      </div>
    </section>
  );
}
```

- [ ] **Step 4: Mount it and adopt the renamed call**

In `frontend/src/components/settings/settings-view.tsx`:

1. Change `instanceSettings.update(` to `instanceSettings.updateDefaultSite(`.
2. Keep the loaded row in state so the card can be seeded: add
   `const [settings, setSettings] = useState<InstanceSettings | null>(null);`,
   set it in `load` alongside the form state, and update it in `handleSave`.
3. Render the card below the Default site section:

```tsx
        {settings ? (
          <LlmCard settings={settings} onSaved={setSettings} />
        ) : null}
```

Import `LlmCard` and the `InstanceSettings` type.

In `frontend/src/components/settings/settings-view.test.tsx`, add the four
`llm_*` fields to `makeSettings`, change the `instanceSettings.update` spy to
`updateDefaultSite`, add spies for `updateLlm` and `testLlm`, and update the two
assertions naming `instanceSettings.update`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/settings/`
Expected: PASS.

If a `getByLabelText` is ambiguous, check whether two controls share an
accessible name and rename the *field* — that happened with "Custom page" in the
default-site round and it was a real UI wart, not a test problem.

- [ ] **Step 6: Full frontend verification**

```bash
cd frontend && npx vitest run && npm run typecheck && npm run lint && npm run build
```

- [ ] **Step 7: Check it in the real app**

```bash
docker compose up -d --build
```

Open Settings and, against a real provider key:

- Enable, enter a model and key, **Test connection** — expect the reply and a latency.
- Break the key deliberately and test again — expect the error inline, and
  confirm **the key does not appear anywhere in it**.
- Save, reload the page — the key field is empty and reads "A key is stored."
- Save again without retyping the key, then test — still connects, proving
  omit-to-keep works end to end.
- **Remove stored key**, save, reload — reads "No key stored."
- Check `docker compose logs backend | grep -i "sk-"` finds nothing.

- [ ] **Step 8: Line endings and commit**

```bash
git ls-files --eol frontend/src/components/settings/llm-card.tsx frontend/src/components/settings/llm-card.test.tsx frontend/src/components/settings/settings-view.tsx frontend/src/components/settings/settings-view.test.tsx
git add frontend/src/components/settings
git commit -m "feat(llm): configure and test the LLM integration from Settings"
```

---

## Done when

- Every task's steps are checked off.
- `docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings` — all pass, no new skips.
- `docker exec megoopm-test ruff check app tests alembic` — clean.
- `cd frontend && npx vitest run && npm run typecheck && npm run lint && npm run build` — all pass.
- `git ls-files --eol` shows no `w/crlf` on any changed file.
- The migration was verified on a fresh database, downgrade round trip included.
- The manual pass in Task 6 Step 7 has been walked against a real provider,
  including the key-does-not-leak checks.
- Test containers torn down: `docker rm -f megoopm-test megoopm-testdb && docker network rm megoopm-testnet`.

## Follow-up, not in this plan

Part B — AI-assisted editing in the Custom Pages editor — is the reason this
exists and is its own design round. It will want streaming, which
`app/services/llm.py` does not yet do; adding it there is a second function
beside `complete`, not a redesign.
