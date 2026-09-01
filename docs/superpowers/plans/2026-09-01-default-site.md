# Default Site Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator choose from the Settings page what nginx returns for a request matching no configured host — a themed congratulations page, a bare 404, no response at all, a redirect, or one of the Custom Pages authored in the app.

**Architecture:** A typed singleton row (`instance_settings`, always `id=1`) holds the choice. A pure renderer turns it into a `location /` fragment (plus an HTML file for the two document modes) written into `/data/nginx/default/`, a new third reconciliation target for the existing apply engine. The read-only base `nginx.conf` swaps its hardcoded `location / { return 404; }` for `include /data/nginx/default/*.conf;` — with no file present nginx's own no-location-match behaviour returns 404, so the fallback is byte-identical to today.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 + Alembic, Jinja2, Pydantic v2; Next.js 16 + React 19 + base-ui + Tailwind v4 + vitest on the frontend.

**Spec:** `docs/superpowers/specs/2026-09-01-default-site-design.md`

## Global Constraints

- **Backend tests only run on Linux** — `app` imports `fcntl`. Use the containerised runner in "Running the backend tests" below. Never run `pytest` on the Windows host.
- **Most tests here need a reachable Postgres.** Without one the API suites silently *skip*. The runner below starts one.
- **`op.create_check_constraint` takes the BARE constraint name.** The `ck_%(table_name)s_%(constraint_name)s` convention is applied by Alembic on top; passing an expanded name double-prefixes it.
- **The character check must run BEFORE `urlsplit`.** Python's `urlsplit` silently *strips* tab, CR and LF per WHATWG before parsing, so a URL containing a newline parses clean and the newline survives into the rendered config. Validate the raw string first.
- **Line endings must be LF.** The Edit/Write tools on this machine can emit CRLF and `git status` hides it. After editing, run `git ls-files --eol <files>`; anything showing `w/crlf` gets `sed -i 's/\r$//'`.
- **Schema changes need two regenerations:** `docker exec megoopm-test python -m scripts.export_openapi`, then `cd frontend && npm run gen:api`. `tests/test_openapi.py` fails until the first is done.
- **`ruff format --check .` reports ~32 pre-existing unformatted files on HEAD.** Only format files you create; never reformat a file you did not otherwise touch.
- **vitest does not typecheck** — run `npm run typecheck` separately.
- Frontend commands run from `frontend/`: `npx vitest run`, `npm run lint`, `npm run typecheck`.
- **Never hardcode hex/oklch in frontend components** — they resolve to the CSS variables in `globals.css`. The one exception is the congratulations page (Task 4), which is standalone HTML served by nginx and cannot reach those variables.
- **Branch:** the operator has committed directly to `main` for the last two features. An eight-commit feature is a reasonable place to branch instead — **confirm before Task 1's commit.**

### Two deliberate refinements to the spec

1. **`InstanceSettingsUpdate` requires `default_site_mode`.** The spec says Pydantic mirrors the two CHECK constraints as model validators, but a partial `PATCH` cannot: sending `{"default_site_mode": "redirect"}` alone is only incoherent *relative to the stored row*, which a schema never sees. Requiring the mode makes the payload self-describing, so the validator is complete and state-free — and it matches the UI, where one Save button submits the whole card.

2. **`DELETE /custom-pages/{id}` needs no reload branch, only the 409.** The spec says both `PATCH` and `DELETE` should conditionally reload. But `ON DELETE RESTRICT` means deleting a *referenced* page is impossible; any delete that succeeds was of an unreferenced page and therefore changed no config. Only `PATCH` needs the conditional.

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

Then per test run (note: **no `-q`** — `pyproject.toml` already sets it, and `-qq` hides the pass count):

```bash
docker exec megoopm-test python -m pytest tests/<file> -p no:cacheprovider -p no:warnings
docker exec megoopm-test ruff check app tests alembic
```

Teardown: `docker rm -f megoopm-test megoopm-testdb && docker network rm megoopm-testnet`

---

## File Structure

**Created:**

| file | responsibility |
| --- | --- |
| `backend/alembic/versions/0019_instance_settings.py` | table, enum type, constraints, seeded row |
| `backend/app/models/instance_settings.py` | `InstanceSettings` |
| `backend/app/schemas/instance_settings.py` | request/response models + redirect-URL validator |
| `backend/app/services/instance_settings.py` | read/update the singleton, clear stale fields |
| `backend/app/api/routes/settings.py` | `GET` / `PATCH /api/v1/settings` |
| `backend/app/templates/nginx/default_site.conf.j2` | the `location /` fragment |
| `backend/app/templates/nginx/congratulations.html.j2` | the themed default page |
| `backend/tests/test_instance_settings_schema.py` | redirect-URL validator, coherence rules (pure) |
| `backend/tests/test_settings_api.py` | routes, reload behaviour, custom-page coupling |
| `backend/tests/test_default_site_render.py` | one case per mode (pure) |
| `frontend/src/lib/api/resources/settings.ts` | typed client |
| `frontend/src/components/ui/radio-group.tsx` | base-ui radio primitive |
| `frontend/src/components/settings/lib.ts` | pure form state + validation |
| `frontend/src/components/settings/lib.test.ts` | its tests |
| `frontend/src/components/settings/settings-view.tsx` | the Default site card |
| `frontend/src/components/settings/settings-view.test.tsx` | its tests |

**Modified:**

| file | change |
| --- | --- |
| `backend/app/models/enums.py` | `DefaultSiteMode` |
| `backend/app/models/__init__.py` | register `InstanceSettings` |
| `backend/app/api/router.py` | mount the settings router |
| `backend/app/api/routes/custom_pages.py` | 409 on delete-while-referenced; conditional reload on patch |
| `backend/app/services/nginx/state.py` | `DefaultSiteSpec`, `DesiredState.default_site` |
| `backend/app/services/nginx/renderer.py` | `render_default_site` |
| `backend/app/services/nginx/loader.py` | resolve the setting and its page |
| `backend/app/services/nginx/engine.py` | third apply target |
| `backend/app/services/nginx/__init__.py` | export `render_default_site` |
| `backend/app/tasks/nginx.py` | pass `default_dir` in both apply paths |
| `backend/app/core/config.py` | `nginx_default_dir` |
| `backend/openapi.json` | regenerated |
| `infra/nginx/nginx.conf` | swap `location /` for the include |
| `docker-compose.yml`, `docker-compose.ha.yml` | `data-init` creates `/data/nginx/default` |
| `docs/data-model.md`, `docs/nginx-engine.md` | document the table and the third target |
| `backend/tests/test_nginx_engine.py` | default-site apply cases, reusing its `FakeController` |
| `backend/tests/test_custom_pages_api.py` | delete-while-referenced and conditional-reload cases |
| `frontend/src/lib/api/index.ts` | export the settings resource |
| `frontend/src/app/(app)/settings/page.tsx` | mount `SettingsView` |
| `frontend/src/lib/api/generated/schema.ts` | regenerated |

---

### Task 1: Data model, enum and migration

**Files:**
- Create: `backend/alembic/versions/0019_instance_settings.py`, `backend/app/models/instance_settings.py`
- Modify: `backend/app/models/enums.py`, `backend/app/models/__init__.py`, `docs/data-model.md`
- Test: `backend/tests/test_settings_api.py` (created here with the model round-trip only; routes land in Task 3)

**Interfaces:**
- Produces: `DefaultSiteMode` (StrEnum: `congratulations`, `not_found`, `no_response`, `redirect`, `custom_page`); `InstanceSettings` ORM model with `id`, `default_site_mode`, `default_site_redirect_url`, `default_site_page_id`, `created_at`, `updated_at`. Migration revision id `0019_instance_settings`, down-revision `0018_custom_pages`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_settings_api.py`. Copy the `pg_conn` / `client` / `admin_token` / `auth` fixture block verbatim from `backend/tests/test_custom_pages_api.py` lines 30–97, changing only the admin email to `settings-admin@example.com`. Then add:

```python
from sqlalchemy import text


async def test_migration_seeds_one_row_preserving_todays_behaviour(pg_conn) -> None:
    """A fresh instance must keep serving 404, not silently switch to a new page."""
    result = await pg_conn.execute(
        text("SELECT id, default_site_mode FROM instance_settings")
    )
    rows = result.all()
    assert len(rows) == 1
    assert rows[0].id == 1
    assert rows[0].default_site_mode == "not_found"


async def test_redirect_without_a_url_is_rejected_by_the_database(pg_conn) -> None:
    """A half-configured row would render a config that says nothing."""
    with pytest.raises(Exception):
        await pg_conn.execute(
            text(
                "UPDATE instance_settings SET default_site_mode = 'redirect', "
                "default_site_redirect_url = NULL WHERE id = 1"
            )
        )
```

Note: the `pg_conn` fixture builds tables with `Base.metadata.create_all`, which does **not** run migrations — so the seed row must also be inserted by a fixture for this first test. Add to the file, after the fixtures:

```python
@pytest.fixture(autouse=True)
async def seeded_settings(pg_conn):
    """`create_all` builds the table but does not run the migration's seed."""
    await pg_conn.execute(
        text("INSERT INTO instance_settings (id, default_site_mode) VALUES (1, 'not_found')")
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_settings_api.py -p no:cacheprovider -p no:warnings`
Expected: FAIL — `relation "instance_settings" does not exist`.

- [ ] **Step 3: Add the enum**

In `backend/app/models/enums.py`, after `AccessListDirective`:

```python
class DefaultSiteMode(enum.StrEnum):
    """What nginx returns for a request matching no configured host."""

    congratulations = "congratulations"
    not_found = "not_found"
    no_response = "no_response"
    redirect = "redirect"
    custom_page = "custom_page"
```

Add `"DefaultSiteMode"` to that module's `__all__`, keeping it alphabetical.

- [ ] **Step 4: Add the model**

Create `backend/app/models/instance_settings.py`:

```python
"""Instance-wide settings — a single row the whole deployment shares.

One row, always ``id=1``, seeded by the migration so readers never handle "no
row yet" (the same shape as ``crowdsec_whitelist_apply``). Settings are typed
columns rather than a key/value blob: this codebase is typed end to end, and a
JSON value would push validation into hand-written per-key code and cost the
frontend its generated types.

Today it holds one setting — the default site, i.e. what nginx returns for a
request matching no configured host.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, Enum, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import DefaultSiteMode
from app.models.mixins import TimestampMixin


class InstanceSettings(TimestampMixin, Base):
    """The singleton settings row."""

    __tablename__ = "instance_settings"
    __table_args__ = (
        # A half-configured row renders nginx config that says nothing, so the
        # database refuses it as well as the API. Bare names: the metadata
        # naming convention adds the ck_%(table_name)s_ prefix.
        CheckConstraint(
            "default_site_mode <> 'redirect' OR default_site_redirect_url IS NOT NULL",
            name="redirect_needs_url",
        ),
        CheckConstraint(
            "default_site_mode <> 'custom_page' OR default_site_page_id IS NOT NULL",
            name="custom_page_needs_page",
        ),
    )

    # Not autoincrement: there is exactly one row and its id is always 1.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False, default=1)

    default_site_mode: Mapped[DefaultSiteMode] = mapped_column(
        Enum(
            DefaultSiteMode,
            name="default_site_mode",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=DefaultSiteMode.not_found,
        server_default=DefaultSiteMode.not_found.value,
    )
    default_site_redirect_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # RESTRICT, not SET NULL: silently changing what every unmatched visitor
    # sees is worse than refusing the delete. (Contrast proxy_hosts.access_list_id,
    # where detaching one host's guard is visible and recoverable.)
    default_site_page_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("custom_pages.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )


__all__ = ["InstanceSettings"]
```

Register it in `backend/app/models/__init__.py`: add `from app.models.instance_settings import InstanceSettings  # noqa: F401` in alphabetical position (after `dns_credential`, before `proxy_host`), and `"InstanceSettings"` to `__all__`.

- [ ] **Step 5: Write the migration**

Create `backend/alembic/versions/0019_instance_settings.py`:

```python
"""Instance-wide settings singleton, holding the default site

One seeded row (``id=1``) so readers never handle "no row yet". Seeded as
``not_found``, which is exactly what the base nginx config hardcodes today —
seeding ``congratulations`` would match NPM's default but would silently change
what a live instance serves the moment this migration runs.

Revision ID: 0019_instance_settings
Revises: 0018_custom_pages
Create Date: 2026-09-01 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019_instance_settings"
down_revision: str | None = "0018_custom_pages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MODE = sa.Enum(
    "congratulations",
    "not_found",
    "no_response",
    "redirect",
    "custom_page",
    name="default_site_mode",
)


def upgrade() -> None:
    _MODE.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "instance_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("default_site_mode", _MODE, nullable=False, server_default="not_found"),
        sa.Column("default_site_redirect_url", sa.Text(), nullable=True),
        sa.Column("default_site_page_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_foreign_key(
        op.f("fk_instance_settings_default_site_page_id_custom_pages"),
        "instance_settings",
        "custom_pages",
        ["default_site_page_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_instance_settings_default_site_page_id"),
        "instance_settings",
        ["default_site_page_id"],
    )
    # Bare names: the ck_%(table_name)s_%(constraint_name)s convention is
    # applied by alembic, so an expanded name would be double-prefixed.
    op.create_check_constraint(
        "redirect_needs_url",
        "instance_settings",
        "default_site_mode <> 'redirect' OR default_site_redirect_url IS NOT NULL",
    )
    op.create_check_constraint(
        "custom_page_needs_page",
        "instance_settings",
        "default_site_mode <> 'custom_page' OR default_site_page_id IS NOT NULL",
    )
    # Seed the singleton so readers never have to handle "no row yet".
    op.execute("INSERT INTO instance_settings (id, default_site_mode) VALUES (1, 'not_found')")


def downgrade() -> None:
    op.drop_table("instance_settings")
    _MODE.drop(op.get_bind(), checkfirst=True)
```

- [ ] **Step 6: Run the tests**

Run: `docker exec megoopm-test python -m pytest tests/test_settings_api.py -p no:cacheprovider -p no:warnings`
Expected: PASS (2 tests).

- [ ] **Step 7: Verify the migration against a fresh database**

The suite builds tables with `create_all` and never exercises migrations, so this is a separate check.

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
docker exec megoopm-migdb psql -U megoopm -d megoopm -c "SELECT * FROM instance_settings"
docker exec megoopm-mig alembic downgrade -1 && docker exec megoopm-mig alembic upgrade head
docker rm -f megoopm-mig megoopm-migdb
```

Expected: table has both CHECK constraints and the RESTRICT foreign key; `SELECT` returns exactly one row with `id=1`, `default_site_mode=not_found`; the downgrade/re-upgrade round trip succeeds.

- [ ] **Step 8: Document the table**

In `docs/data-model.md`, add `instance_settings` following the format of the tables already listed: columns, the two CHECK constraints, and one line noting it is a seeded singleton whose page FK is `RESTRICT` because the default site is instance-wide.

- [ ] **Step 9: Lint, check line endings, commit**

```bash
docker exec megoopm-test ruff check app tests alembic
docker exec megoopm-test ruff format --check app/models/instance_settings.py alembic/versions/0019_instance_settings.py tests/test_settings_api.py
git ls-files --eol backend/app/models/instance_settings.py backend/app/models/enums.py backend/app/models/__init__.py backend/alembic/versions/0019_instance_settings.py backend/tests/test_settings_api.py
git add backend/app/models backend/alembic/versions/0019_instance_settings.py backend/tests/test_settings_api.py docs/data-model.md
git commit -m "feat(settings): add the instance-settings singleton holding the default site"
```

---

### Task 2: Schemas and the redirect-URL validator

**Files:**
- Create: `backend/app/schemas/instance_settings.py`, `backend/tests/test_instance_settings_schema.py`

**Interfaces:**
- Consumes: `DefaultSiteMode` (Task 1).
- Produces: `validate_redirect_url(value: str) -> str`; `InstanceSettingsRead` (fields `default_site_mode`, `default_site_redirect_url`, `default_site_page_id`, `updated_at`); `InstanceSettingsUpdate` (`default_site_mode` **required**, `default_site_redirect_url: str | None`, `default_site_page_id: int | None`).

This task is its own gate because the redirect URL is the one piece of operator input that lands inside a generated nginx config. `nginx -t` and rollback catch a config that fails to *parse*; they do nothing about one that parses fine and does something else.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_instance_settings_schema.py`:

```python
"""Schema tests for instance settings.

The redirect URL is operator input that lands verbatim inside a generated nginx
config file, so its validator gets a case per rejected class rather than one
happy-path test.
"""

from __future__ import annotations

import pytest
from app.schemas.instance_settings import InstanceSettingsUpdate, validate_redirect_url
from pydantic import ValidationError


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://example.com",
        "https://example.com/path?q=1",
        "https://example.com:8443/deep/path",
    ],
)
def test_accepts_plain_absolute_urls(url: str) -> None:
    assert validate_redirect_url(url) == url


def test_trims_surrounding_whitespace() -> None:
    assert validate_redirect_url("  https://example.com  ") == "https://example.com"


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("ftp://example.com", "scheme"),
        ("file:///etc/passwd", "scheme"),
        ("/relative/path", "scheme"),
        ("javascript:alert(1)", "scheme"),
        ("https://", "host"),
    ],
)
def test_rejects_targets_that_are_not_absolute_http_urls(url: str, why: str) -> None:
    with pytest.raises(ValueError):
        validate_redirect_url(url)


@pytest.mark.parametrize(
    "url",
    [
        'https://example.com" ; return 200 "pwned',   # quote + directive break
        "https://example.com';",                       # single quote
        "https://example.com\\",                       # backslash
        "https://example.com;",                        # directive terminator
        "https://example.com$request_uri",             # nginx variable
        "https://example.com\nlocation / { return 200; }",  # newline
        "https://example.com\rX",                      # carriage return
        "https://example.com\tX",                      # tab
    ],
)
def test_rejects_nginx_config_injection(url: str) -> None:
    """These parse as URLs; the point is that they must never reach the config."""
    with pytest.raises(ValueError):
        validate_redirect_url(url)


def test_newline_is_caught_before_urlsplit_can_strip_it() -> None:
    """urlsplit removes CR/LF/TAB per WHATWG, so parsing first would pass this."""
    from urllib.parse import urlsplit

    hostile = "https://example.com\nreturn 200;"
    assert urlsplit(hostile).scheme == "https"  # parses clean — the trap
    with pytest.raises(ValueError):
        validate_redirect_url(hostile)


def test_redirect_mode_requires_a_url() -> None:
    with pytest.raises(ValidationError):
        InstanceSettingsUpdate(default_site_mode="redirect")


def test_custom_page_mode_requires_a_page() -> None:
    with pytest.raises(ValidationError):
        InstanceSettingsUpdate(default_site_mode="custom_page")


def test_simple_modes_need_nothing_else() -> None:
    for mode in ("congratulations", "not_found", "no_response"):
        assert InstanceSettingsUpdate(default_site_mode=mode).default_site_mode == mode


def test_mode_is_required() -> None:
    """A partial patch cannot be checked for coherence without the stored row."""
    with pytest.raises(ValidationError):
        InstanceSettingsUpdate(default_site_redirect_url="https://example.com")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_instance_settings_schema.py -p no:cacheprovider -p no:warnings`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.schemas.instance_settings'`.

- [ ] **Step 3: Write the schemas**

Create `backend/app/schemas/instance_settings.py`:

```python
"""Pydantic schemas for instance settings.

``default_site_redirect_url`` is the one field here that becomes part of a
generated nginx configuration file, so it is validated far more strictly than a
URL field normally would be — see :func:`validate_redirect_url`.

:class:`InstanceSettingsUpdate` requires ``default_site_mode`` even though it is
otherwise a partial update. Coherence ("redirect needs a URL") cannot be checked
against a payload that omits the mode, because whether the rule applies depends
on the stored row, which a schema never sees. Requiring the mode makes the
payload self-describing and matches the UI, where one Save button submits the
whole card.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import DefaultSiteMode

_ALLOWED_SCHEMES = {"http", "https"}

# Characters that would let a redirect target escape its nginx directive:
# quotes close the quoted string, a backslash escapes, ';' ends the directive,
# and '$' interpolates an nginx variable into the target.
_FORBIDDEN = frozenset('"\'\\;$')


def validate_redirect_url(value: str) -> str:
    """Accept only a plain absolute http(s) URL that is inert inside nginx config.

    The order matters: the character scan runs **before** :func:`urlsplit`,
    because Python strips tab, CR and LF from a URL before parsing it (WHATWG
    behaviour). A target containing a newline therefore parses perfectly
    cleanly, and parsing first would let it through into the rendered config
    where the newline ends the ``return`` directive and begins a new one.
    """
    stripped = value.strip()
    if not stripped:
        raise ValueError("redirect URL must not be empty")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in stripped):
        raise ValueError("redirect URL must not contain control characters")
    if any(c in _FORBIDDEN for c in stripped):
        raise ValueError("redirect URL must not contain quotes, a backslash, ';' or '$'")

    parsed = urlsplit(stripped)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ValueError("redirect URL must start with http:// or https://")
    if not parsed.netloc:
        raise ValueError("redirect URL must include a host")
    return stripped


class InstanceSettingsRead(BaseModel):
    """Public representation of the settings singleton."""

    model_config = ConfigDict(from_attributes=True)

    default_site_mode: DefaultSiteMode
    default_site_redirect_url: str | None
    default_site_page_id: int | None
    updated_at: datetime


class InstanceSettingsUpdate(BaseModel):
    """Set the default site. ``default_site_mode`` is required (see module doc)."""

    default_site_mode: DefaultSiteMode
    default_site_redirect_url: str | None = Field(
        default=None, description="Required when the mode is 'redirect'"
    )
    default_site_page_id: int | None = Field(
        default=None, description="Required when the mode is 'custom_page'"
    )

    @field_validator("default_site_redirect_url")
    @classmethod
    def _clean_url(cls, value: str | None) -> str | None:
        return None if value is None else validate_redirect_url(value)

    @model_validator(mode="after")
    def _coherent(self) -> InstanceSettingsUpdate:
        """Mirror the database CHECK constraints, with a usable message."""
        if self.default_site_mode is DefaultSiteMode.redirect and not self.default_site_redirect_url:
            raise ValueError("default_site_redirect_url is required when the mode is 'redirect'")
        if self.default_site_mode is DefaultSiteMode.custom_page and self.default_site_page_id is None:
            raise ValueError("default_site_page_id is required when the mode is 'custom_page'")
        return self


__all__ = [
    "InstanceSettingsRead",
    "InstanceSettingsUpdate",
    "validate_redirect_url",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker exec megoopm-test python -m pytest tests/test_instance_settings_schema.py -p no:cacheprovider -p no:warnings`
Expected: PASS (all cases).

- [ ] **Step 5: Lint, check line endings, commit**

```bash
docker exec megoopm-test ruff check app tests
docker exec megoopm-test ruff format --check app/schemas/instance_settings.py tests/test_instance_settings_schema.py
git ls-files --eol backend/app/schemas/instance_settings.py backend/tests/test_instance_settings_schema.py
git add backend/app/schemas/instance_settings.py backend/tests/test_instance_settings_schema.py
git commit -m "feat(settings): validate the default-site redirect URL against config injection"
```

---

### Task 3: Service and settings routes

**Files:**
- Create: `backend/app/services/instance_settings.py`, `backend/app/api/routes/settings.py`
- Modify: `backend/app/api/router.py`, `backend/openapi.json`
- Test: `backend/tests/test_settings_api.py` (extend)

**Interfaces:**
- Consumes: `InstanceSettings` (Task 1), `InstanceSettingsRead` / `InstanceSettingsUpdate` (Task 2).
- Produces: `get_instance_settings(db) -> InstanceSettings`; `update_default_site(db, changes: dict) -> InstanceSettings`; routes `GET`/`PATCH /api/v1/settings`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_settings_api.py`:

```python
CUSTOM_HTML = "<!doctype html><html><body>ban</body></html>"


async def _make_page(client: AsyncClient, auth, name: str = "Denied") -> int:
    resp = await client.post(
        "/api/v1/custom-pages", headers=auth, json={"name": name, "html": CUSTOM_HTML}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_get_returns_the_seeded_default(client: AsyncClient, auth) -> None:
    resp = await client.get("/api/v1/settings", headers=auth)
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_site_mode"] == "not_found"
    assert resp.json()["default_site_redirect_url"] is None


@pytest.mark.parametrize("mode", ["congratulations", "not_found", "no_response"])
async def test_simple_modes_round_trip(client: AsyncClient, auth, mode: str) -> None:
    resp = await client.patch(
        "/api/v1/settings", headers=auth, json={"default_site_mode": mode}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_site_mode"] == mode


async def test_redirect_mode_round_trips(client: AsyncClient, auth) -> None:
    resp = await client.patch(
        "/api/v1/settings",
        headers=auth,
        json={
            "default_site_mode": "redirect",
            "default_site_redirect_url": "https://example.com/moved",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_site_redirect_url"] == "https://example.com/moved"


async def test_custom_page_mode_round_trips(client: AsyncClient, auth) -> None:
    page_id = await _make_page(client, auth)
    resp = await client.patch(
        "/api/v1/settings",
        headers=auth,
        json={"default_site_mode": "custom_page", "default_site_page_id": page_id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_site_page_id"] == page_id


async def test_switching_mode_clears_the_previous_mode_field(client: AsyncClient, auth) -> None:
    """A stale URL would reappear in the form if the operator switched back."""
    await client.patch(
        "/api/v1/settings",
        headers=auth,
        json={
            "default_site_mode": "redirect",
            "default_site_redirect_url": "https://example.com",
        },
    )
    resp = await client.patch(
        "/api/v1/settings", headers=auth, json={"default_site_mode": "not_found"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_site_redirect_url"] is None


async def test_incoherent_payloads_are_rejected(client: AsyncClient, auth) -> None:
    for body in (
        {"default_site_mode": "redirect"},
        {"default_site_mode": "custom_page"},
        {"default_site_mode": "redirect", "default_site_redirect_url": "not-a-url"},
    ):
        resp = await client.patch("/api/v1/settings", headers=auth, json=body)
        assert resp.status_code == 422, (body, resp.text)


async def test_unknown_page_is_rejected(client: AsyncClient, auth) -> None:
    resp = await client.patch(
        "/api/v1/settings",
        headers=auth,
        json={"default_site_mode": "custom_page", "default_site_page_id": 9999},
    )
    assert resp.status_code == 422, resp.text


async def test_a_write_enqueues_exactly_one_reload(client: AsyncClient, auth, monkeypatch) -> None:
    calls = 0

    def _counting_reload() -> TaskEnqueued:
        nonlocal calls
        calls += 1
        return TaskEnqueued(task_id="test-reload-task", status="PENDING")

    monkeypatch.setattr(config_writes, "enqueue_nginx_reload", _counting_reload)

    resp = await client.patch(
        "/api/v1/settings", headers=auth, json={"default_site_mode": "no_response"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["X-Config-Reload-Task"] == "test-reload-task"
    assert calls == 1


async def test_endpoints_require_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/settings")).status_code == 401
    assert (
        await client.patch("/api/v1/settings", json={"default_site_mode": "not_found"})
    ).status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_settings_api.py -p no:cacheprovider -p no:warnings`
Expected: FAIL — the new tests get 404 (route not registered); the two Task 1 tests still pass.

- [ ] **Step 3: Write the service**

Create `backend/app/services/instance_settings.py`:

```python
"""Instance-settings domain service.

One row, always ``id=1``, seeded by migration ``0019``. No FastAPI imports —
callers pass an :class:`~sqlalchemy.ext.asyncio.AsyncSession`.

Setting the default site clears the columns the new mode does not use. The
database CHECK constraints only require that the *relevant* column is present,
so a stale redirect URL could otherwise survive a switch to ``not_found``:
invisible in the rendered config, but it would reappear in the form if the
operator switched back, showing a URL they believed they had left behind.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import DefaultSiteMode
from app.models.instance_settings import InstanceSettings

SETTINGS_ID = 1


class UnknownCustomPageError(Exception):
    """Raised when the default site references a custom page that does not exist."""


async def get_instance_settings(db: AsyncSession) -> InstanceSettings:
    """Return the singleton, creating it if a hand-migrated database lacks it."""
    row = await db.get(InstanceSettings, SETTINGS_ID)
    if row is None:
        row = InstanceSettings(id=SETTINGS_ID, default_site_mode=DefaultSiteMode.not_found)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def update_default_site(db: AsyncSession, changes: dict[str, Any]) -> InstanceSettings:
    """Apply a coherent default-site payload, clearing the unused columns."""
    row = await get_instance_settings(db)
    mode = changes["default_site_mode"]

    row.default_site_mode = mode
    row.default_site_redirect_url = (
        changes.get("default_site_redirect_url") if mode is DefaultSiteMode.redirect else None
    )
    row.default_site_page_id = (
        changes.get("default_site_page_id") if mode is DefaultSiteMode.custom_page else None
    )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        # The only FK here is the custom page, so a violation means the id is bogus.
        raise UnknownCustomPageError(str(exc.orig)) from exc
    await db.refresh(row)
    return row


__all__ = [
    "SETTINGS_ID",
    "UnknownCustomPageError",
    "get_instance_settings",
    "update_default_site",
]
```

- [ ] **Step 4: Write the routes**

Create `backend/app/api/routes/settings.py`:

```python
"""Instance-settings routes (admin-only).

There is one settings row, so the path carries no id — ``/settings``, not
``/settings/{id}``. Writes change rendered nginx configuration, so they go
through :func:`~app.api.routes._config_writes.after_config_write`: audited *and*
followed by a regenerate-and-reload, with the task id in
``X-Config-Reload-Task``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import AdminUser, SessionDep
from app.api.routes._config_writes import after_config_write
from app.models.enums import AuditAction
from app.schemas.instance_settings import InstanceSettingsRead, InstanceSettingsUpdate
from app.services import instance_settings as settings_service

router = APIRouter(tags=["settings"])


@router.get("", response_model=InstanceSettingsRead)
async def read_settings(_admin: AdminUser, db: SessionDep) -> InstanceSettingsRead:
    """Read the instance settings. Admin-only."""
    row = await settings_service.get_instance_settings(db)
    return InstanceSettingsRead.model_validate(row)


@router.patch("", response_model=InstanceSettingsRead)
async def update_settings(
    body: InstanceSettingsUpdate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> InstanceSettingsRead:
    """Set the default site. Admin-only.

    ``default_site_mode`` is required; the columns the chosen mode does not use
    are cleared, so the stored row always describes exactly one configuration.
    """
    changes = body.model_dump()
    try:
        row = await settings_service.update_default_site(db, changes)
    except settings_service.UnknownCustomPageError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="default_site_page_id does not reference an existing custom page",
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.update,
        object_type="instance_settings",
        object_id=row.id,
        meta={"default_site_mode": row.default_site_mode.value},
    )
    return InstanceSettingsRead.model_validate(row)


__all__ = ["router"]
```

Wire it in `backend/app/api/router.py`: add `settings` to the `from app.api.routes import (...)` block in alphabetical position (after `redirection_hosts`, before `streams`), and add below the `custom_pages` line:

```python
api_router.include_router(settings.router, prefix="/settings")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `docker exec megoopm-test python -m pytest tests/test_settings_api.py -p no:cacheprovider -p no:warnings`
Expected: PASS (all tests).

- [ ] **Step 6: Regenerate the OpenAPI document**

```bash
docker exec megoopm-test python -m scripts.export_openapi
docker exec megoopm-test python -m pytest tests/test_openapi.py -p no:cacheprovider -p no:warnings
```
Expected: PASS — `test_committed_openapi_is_in_sync` fails until the export is run.

- [ ] **Step 7: Lint, check line endings, commit**

```bash
docker exec megoopm-test ruff check app tests
docker exec megoopm-test ruff format --check app/services/instance_settings.py app/api/routes/settings.py
git ls-files --eol backend/app/services/instance_settings.py backend/app/api/routes/settings.py backend/app/api/router.py backend/openapi.json
git add backend/app/services/instance_settings.py backend/app/api/routes/settings.py backend/app/api/router.py backend/openapi.json backend/tests/test_settings_api.py
git commit -m "feat(settings): expose GET/PATCH /api/v1/settings for the default site"
```

---

### Task 4: Render the default site

**Files:**
- Create: `backend/app/templates/nginx/default_site.conf.j2`, `backend/app/templates/nginx/congratulations.html.j2`, `backend/tests/test_default_site_render.py`
- Modify: `backend/app/services/nginx/state.py`, `backend/app/services/nginx/renderer.py`, `backend/app/services/nginx/__init__.py`

**Interfaces:**
- Produces: `DefaultSiteSpec(mode: str, redirect_url: str = "", html: str = "")`; `DesiredState.default_site: DefaultSiteSpec | None = None`; `render_default_site(state: DesiredState) -> dict[str, str]`; module constants `DEFAULT_SITE_CONF = "megoopm-default.conf"` and `DEFAULT_SITE_HTML = "megoopm-default.html"`.
- Note the spec resolves the *page id* into `html` in the loader (Task 5), not here — the renderer stays a pure function of explicit data with no database reach-through.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_default_site_render.py`:

```python
"""Rendering tests for the default site.

Pure: a DesiredState in, a {filename: contents} map out, no database and no
filesystem, so the whole mode matrix is covered without infrastructure.
"""

from __future__ import annotations

from app.services.nginx.renderer import (
    DEFAULT_SITE_CONF,
    DEFAULT_SITE_HTML,
    render_default_site,
)
from app.services.nginx.state import DefaultSiteSpec, DesiredState


def _state(**kw) -> DesiredState:
    return DesiredState(default_site=DefaultSiteSpec(**kw))


def test_no_setting_renders_nothing() -> None:
    """With no file, nginx matches no location and returns 404 — today's behaviour."""
    assert render_default_site(DesiredState()) == {}


def test_not_found_returns_404() -> None:
    files = render_default_site(_state(mode="not_found"))
    assert set(files) == {DEFAULT_SITE_CONF}
    assert "return 404;" in files[DEFAULT_SITE_CONF]


def test_no_response_returns_444() -> None:
    files = render_default_site(_state(mode="no_response"))
    assert "return 444;" in files[DEFAULT_SITE_CONF]


def test_redirect_emits_a_quoted_target() -> None:
    files = render_default_site(
        _state(mode="redirect", redirect_url="https://example.com/moved")
    )
    assert 'return 301 "https://example.com/moved";' in files[DEFAULT_SITE_CONF]


def test_custom_page_writes_the_document_verbatim() -> None:
    html = "<!doctype html><html><body>banned</body></html>"
    files = render_default_site(_state(mode="custom_page", html=html))
    assert set(files) == {DEFAULT_SITE_CONF, DEFAULT_SITE_HTML}
    assert files[DEFAULT_SITE_HTML] == html
    assert "try_files /megoopm-default.html =404;" in files[DEFAULT_SITE_CONF]


def test_congratulations_ships_the_bundled_page() -> None:
    files = render_default_site(_state(mode="congratulations"))
    assert set(files) == {DEFAULT_SITE_CONF, DEFAULT_SITE_HTML}
    page = files[DEFAULT_SITE_HTML]
    assert page.startswith("<!doctype html>")
    assert "MegooPM" in page
    # Jinja must not have mangled the CSS braces.
    assert "{{" not in page and "{%" not in page


def test_the_two_document_modes_share_one_conf() -> None:
    """They differ only in file content, so a divergence here is a bug."""
    a = render_default_site(_state(mode="congratulations"))[DEFAULT_SITE_CONF]
    b = render_default_site(_state(mode="custom_page", html="<p>x</p>"))[DEFAULT_SITE_CONF]
    assert a.replace("congratulations", "MODE") == b.replace("custom_page", "MODE")


def test_congratulations_page_makes_no_external_requests() -> None:
    """It is what you see when nothing works; it must not need the network."""
    page = render_default_site(_state(mode="congratulations"))[DEFAULT_SITE_HTML]
    for token in ("http://", "https://", "//fonts.", "<img", "<script"):
        assert token not in page, token


def test_congratulations_page_supports_both_colour_schemes() -> None:
    page = render_default_site(_state(mode="congratulations"))[DEFAULT_SITE_HTML]
    assert "prefers-color-scheme: dark" in page
    # Every oklch is preceded by a hex fallback for pre-2023 browsers.
    assert page.count("oklch(") > 0
    assert page.count("#") >= page.count("oklch(")


def test_filenames_are_sorted() -> None:
    files = render_default_site(_state(mode="congratulations"))
    assert list(files) == sorted(files)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_default_site_render.py -p no:cacheprovider -p no:warnings`
Expected: FAIL — `ImportError: cannot import name 'DEFAULT_SITE_CONF'`.

- [ ] **Step 3: Add the spec to the state module**

In `backend/app/services/nginx/state.py`, after `StreamSpec`:

```python
@dataclass(frozen=True, slots=True)
class DefaultSiteSpec:
    """What nginx answers for a request matching no configured host.

    ``html`` is already resolved: the loader reads the referenced custom page's
    document and puts it here, so the renderer never reaches into the database
    and the whole mode matrix stays unit-testable without one.
    """

    # One of DefaultSiteMode's values, as a plain string — specs stay free of
    # ORM enums so they remain trivially constructible in tests.
    mode: str
    redirect_url: str = ""
    html: str = ""
```

Add to `DesiredState`:

```python
    default_site: DefaultSiteSpec | None = None
```

and extend the `DesiredState` docstring with one sentence: *"``default_site`` renders into a third directory the base config includes from inside its `default_server` block; ``None`` means no file is written and nginx falls back to its own no-location-match 404."*

Add `"DefaultSiteSpec"` to `__all__`.

- [ ] **Step 4: Write the nginx fragment template**

Create `backend/app/templates/nginx/default_site.conf.j2`:

```jinja
{# Renders the `location /` fragment for the default site.

   This file is included INSIDE the base config's `default_server` block, so it
   contains a bare location — not a server block. The base config declares no
   `location /` of its own: when this file is absent nginx matches no location
   and answers 404, which is exactly the behaviour it hardcoded before.

   The two document modes (congratulations, custom_page) differ only in the
   contents of megoopm-default.html, so they share one block here. #}
# Managed by MegooPM — do not edit by hand.
# default site mode={{ site.mode }}
{%- if site.mode == "not_found" %}

location / {
    return 404;
}
{%- elif site.mode == "no_response" %}

location / {
    return 444;
}
{%- elif site.mode == "redirect" %}

location / {
    # Quoted as defence in depth; the API already rejects any target containing
    # a quote, backslash, ';' or '$' (see schemas/instance_settings.py).
    return 301 "{{ site.redirect_url }}";
}
{%- else %}

location / {
    # try_files with an absolute path serves this one document for every URI.
    root {{ default_dir }};
    try_files /megoopm-default.html =404;
}
{%- endif %}
```

- [ ] **Step 5: Write the congratulations page**

Create `backend/app/templates/nginx/congratulations.html.j2`. It has **no Jinja variables** — keep `{{` and `{%` out of it entirely, or Jinja will eat the CSS.

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>MegooPM</title>
    <style>
      /*
       * MegooPM default site. Palette lifted from frontend/src/app/globals.css,
       * which is the single source of truth for the design system.
       *
       * Every oklch() is preceded by a hex fallback: this page is reachable by
       * anything that resolves to the proxy, including browsers predating oklch
       * (Chrome 111 / Safari 15.4 / Firefox 113, 2023). Old browsers take the
       * hex and ignore the oklch that follows; new ones take the oklch. The
       * hex values are approximations — the oklch values are authoritative.
       *
       * No external requests at all: no webfont link, no CDN, no images. This
       * is what a visitor sees when nothing is configured, so it must not need
       * the network to render.
       */

      /* Light — "daylight city". */
      :root {
        --bg: #f3f6f8;
        --bg: oklch(0.975 0.01 220);
        --fg: #272040;
        --fg: oklch(0.22 0.05 290);
        --card: #ffffff;
        --card: oklch(1 0 0);
        --primary: #00778f;
        --primary: oklch(0.5 0.14 205);
        --muted: #6a6b85;
        --muted: oklch(0.48 0.05 260);
        --accent: #ab0069;
        --accent: oklch(0.45 0.22 340);
        --border: #cbd7de;
        --border: oklch(0.86 0.04 210);
        --grid: rgba(0, 119, 143, 0.06);
        --glow: none;
      }

      /*
       * Dark — "neon noir". A standalone page has no next-themes and no toggle,
       * so the visitor's OS preference is the only signal available.
       */
      @media (prefers-color-scheme: dark) {
        :root {
          --bg: #15121e;
          --bg: oklch(0.15 0.03 285);
          --fg: #e8f2f4;
          --fg: oklch(0.95 0.02 200);
          --card: #1c1829;
          --card: oklch(0.19 0.035 285);
          --primary: #4fdcef;
          --primary: oklch(0.85 0.16 195);
          --muted: #9fb2bc;
          --muted: oklch(0.72 0.04 215);
          --accent: #ff77c9;
          --accent: oklch(0.88 0.17 340);
          --border: #2b3f49;
          --border: oklch(0.85 0.16 195 / 18%);
          --grid: rgba(79, 220, 239, 0.05);
          --glow: 0 0 24px rgba(79, 220, 239, 0.35);
        }
      }

      * {
        box-sizing: border-box;
      }

      body {
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        padding: 1.5rem;
        background-color: var(--bg);
        /* Faint scanline grid — the cyberpunk cue, done in CSS so the page
           stays a single dependency-free file. */
        background-image: linear-gradient(var(--grid) 1px, transparent 1px),
          linear-gradient(90deg, var(--grid) 1px, transparent 1px);
        background-size: 48px 48px;
        color: var(--fg);
        font: 16px/1.6 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
      }

      main {
        width: 100%;
        max-width: 30rem;
        padding: 2.5rem 2rem;
        border: 1px solid var(--border);
        border-radius: 14px;
        background: var(--card);
        box-shadow: var(--glow);
        text-align: center;
      }

      .wordmark {
        margin: 0 0 1.75rem;
        font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
        font-size: 0.8125rem;
        font-weight: 600;
        letter-spacing: 0.22em;
        text-transform: uppercase;
        color: var(--primary);
      }

      h1 {
        margin: 0 0 0.75rem;
        font-size: 1.5rem;
        line-height: 1.25;
        font-weight: 650;
        letter-spacing: -0.01em;
      }

      .rule {
        width: 3rem;
        height: 2px;
        margin: 1.5rem auto;
        border-radius: 2px;
        background: var(--accent);
      }

      p {
        margin: 0 0 0.75rem;
        color: var(--muted);
        font-size: 0.9375rem;
      }

      p:last-child {
        margin-bottom: 0;
      }

      code {
        font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
        font-size: 0.875em;
        color: var(--fg);
      }

      @media (prefers-reduced-motion: no-preference) {
        main {
          animation: rise 0.4s ease-out;
        }
        @keyframes rise {
          from {
            opacity: 0;
            transform: translateY(6px);
          }
        }
      }
    </style>
  </head>
  <body>
    <main>
      <p class="wordmark">MegooPM</p>
      <h1>You&rsquo;ve reached the proxy</h1>
      <div class="rule"></div>
      <p>
        This request didn&rsquo;t match any configured host, so MegooPM answered
        with its default site.
      </p>
      <p>
        If you were expecting a site here, add a proxy host for this domain in
        the admin panel &mdash; or change what this page shows under
        <code>Settings &rsaquo; Default site</code>.
      </p>
    </main>
  </body>
</html>
```

- [ ] **Step 6: Write the renderer function**

In `backend/app/services/nginx/renderer.py`, add after `render_stream_config`:

```python
DEFAULT_SITE_CONF = "megoopm-default.conf"
DEFAULT_SITE_HTML = "megoopm-default.html"

# The two modes that answer with a document rather than a status code.
_DOCUMENT_MODES = frozenset({"congratulations", "custom_page"})


def render_default_site(state: DesiredState) -> dict[str, str]:
    """Render the default-site files to a ``{filename: contents}`` mapping.

    These are written to a directory the base config includes from *inside* its
    ``default_server`` block, so the ``.conf`` holds a bare ``location``, not a
    server block. An empty mapping is meaningful: with no file present nginx
    matches no location and answers 404, which is what the base config used to
    hardcode.
    """
    site = state.default_site
    if site is None:
        return {}

    files = {
        DEFAULT_SITE_CONF: _env()
        .get_template("default_site.conf.j2")
        .render(site=site, default_dir=settings.nginx_default_dir)
    }
    if site.mode in _DOCUMENT_MODES:
        files[DEFAULT_SITE_HTML] = (
            _env().get_template("congratulations.html.j2").render()
            if site.mode == "congratulations"
            else site.html
        )
    return {name: files[name] for name in sorted(files)}
```

Import `DefaultSiteSpec` is not needed here (only `DesiredState` is used). Add `"render_default_site"`, `"DEFAULT_SITE_CONF"` and `"DEFAULT_SITE_HTML"` to the module's `__all__`, and re-export `render_default_site` from `backend/app/services/nginx/__init__.py` alongside `render_config`.

- [ ] **Step 7: Run test to verify it passes**

Run: `docker exec megoopm-test python -m pytest tests/test_default_site_render.py -p no:cacheprovider -p no:warnings`
Expected: PASS (all tests).

- [ ] **Step 8: Eyeball the page**

The tests check structure, not looks. Render it and open it:

```bash
docker exec megoopm-test python -c "
from app.services.nginx.renderer import render_default_site, DEFAULT_SITE_HTML
from app.services.nginx.state import DefaultSiteSpec, DesiredState
print(render_default_site(DesiredState(default_site=DefaultSiteSpec(mode='congratulations')))[DEFAULT_SITE_HTML])
" > /c/Users/hammad/AppData/Local/Temp/congrats.html
```

Open that file in a browser and toggle your OS between light and dark. Both must be legible with the palette from `globals.css` — cyan-teal brand, magenta rule, violet-tinted near-black in dark.

- [ ] **Step 9: Lint, check line endings, commit**

```bash
docker exec megoopm-test ruff check app tests
docker exec megoopm-test ruff format --check tests/test_default_site_render.py
git ls-files --eol backend/app/services/nginx/state.py backend/app/services/nginx/renderer.py backend/app/services/nginx/__init__.py backend/app/templates/nginx/default_site.conf.j2 backend/app/templates/nginx/congratulations.html.j2 backend/tests/test_default_site_render.py
git add backend/app/services/nginx backend/app/templates/nginx backend/tests/test_default_site_render.py
git commit -m "feat(nginx): render the default site, with a themed congratulations page"
```

---

### Task 5: Wire the renderer into the engine, the loader and nginx

**Files:**
- Modify: `backend/app/core/config.py`, `backend/app/services/nginx/loader.py`, `backend/app/services/nginx/engine.py`, `backend/app/tasks/nginx.py`, `infra/nginx/nginx.conf`, `docker-compose.yml`, `docker-compose.ha.yml`, `docs/nginx-engine.md`
- Test: `backend/tests/test_nginx_engine.py` (engine cases — it already has the `FakeController` and temp-dir idiom), `backend/tests/test_settings_api.py` (the loader case, where the database fixtures live)

**Interfaces:**
- Consumes: `render_default_site`, `DefaultSiteSpec`, `DesiredState.default_site` (Task 4); `InstanceSettings` (Task 1).
- Produces: `settings.nginx_default_dir: str`; `apply_config(..., default_dir: str | os.PathLike[str] | None = None)`.

- [ ] **Step 1: Write the failing engine tests**

Append to `backend/tests/test_nginx_engine.py`, which already has `FakeController` (with `.tests` / `.reloads` counters) and the temp-directory idiom. Add `DefaultSiteSpec` to its existing `from app.services.nginx.state import (...)` block, and add:

```python
from app.services.nginx.renderer import DEFAULT_SITE_CONF, DEFAULT_SITE_HTML


def test_apply_writes_the_default_site_to_its_own_directory(tmp_path: Path) -> None:
    confd = tmp_path / "conf.d"
    default_dir = tmp_path / "default"
    controller = FakeController()

    result = apply_config(
        DesiredState(default_site=DefaultSiteSpec(mode="no_response")),
        confd_dir=confd,
        controller=controller,
        default_dir=default_dir,
    )

    assert result.valid and result.changed
    assert "return 444;" in (default_dir / DEFAULT_SITE_CONF).read_text()
    # One validation covering every directory, not one per directory.
    assert controller.tests == 1


def test_apply_removes_the_html_when_the_mode_stops_needing_it(tmp_path: Path) -> None:
    """A stale megoopm-default.html would be served by nothing but still sit there."""
    confd = tmp_path / "conf.d"
    default_dir = tmp_path / "default"

    apply_config(
        DesiredState(default_site=DefaultSiteSpec(mode="custom_page", html="<p>x</p>")),
        confd_dir=confd,
        controller=FakeController(),
        default_dir=default_dir,
    )
    assert (default_dir / DEFAULT_SITE_HTML).exists()

    apply_config(
        DesiredState(default_site=DefaultSiteSpec(mode="not_found")),
        confd_dir=confd,
        controller=FakeController(),
        default_dir=default_dir,
    )
    assert not (default_dir / DEFAULT_SITE_HTML).exists()


def test_a_bad_default_site_rolls_back_the_other_directories(tmp_path: Path) -> None:
    """All targets share one nginx -t, so none may half-apply."""
    confd = tmp_path / "conf.d"
    default_dir = tmp_path / "default"

    apply_config(
        DesiredState(default_site=DefaultSiteSpec(mode="not_found")),
        confd_dir=confd,
        controller=FakeController(),
        default_dir=default_dir,
    )
    good = (default_dir / DEFAULT_SITE_CONF).read_text()

    result = apply_config(
        _state_with_default(DefaultSiteSpec(mode="no_response")),
        confd_dir=confd,
        controller=FakeController(test_ok=False),
        default_dir=default_dir,
    )
    assert result.rolled_back and not result.valid
    assert (default_dir / DEFAULT_SITE_CONF).read_text() == good
    assert not list(confd.glob("megoopm-proxy-*.conf"))


def test_apply_without_a_default_dir_touches_nothing_new(tmp_path: Path) -> None:
    """Callers that do not opt in keep their current behaviour exactly."""
    confd = tmp_path / "conf.d"
    result = apply_config(
        DesiredState(default_site=DefaultSiteSpec(mode="no_response")),
        confd_dir=confd,
        controller=FakeController(),
    )
    assert result.valid
    assert not (tmp_path / "default").exists()
```

with this helper beside the module's existing `_state()`:

```python
def _state_with_default(site: DefaultSiteSpec) -> DesiredState:
    """The module's usual proxy-host state, plus a default site."""
    base = _state()
    return DesiredState(
        proxy_hosts=base.proxy_hosts,
        http_upstreams=base.http_upstreams,
        default_site=site,
    )
```

- [ ] **Step 1b: Write the failing loader test**

This one needs a database session, so it goes in `backend/tests/test_settings_api.py` where those fixtures already exist — not in the pure render module. Append:

```python
async def test_the_default_site_renders_the_referenced_page(client: AsyncClient, auth) -> None:
    """End to end: setting -> loader -> renderer, with the page's own HTML."""
    from app.services.nginx.loader import load_desired_state
    from app.services.nginx.renderer import DEFAULT_SITE_HTML, render_default_site

    page_id = await _make_page(client, auth, name="Rendered")
    await client.patch(
        "/api/v1/settings",
        headers=auth,
        json={"default_site_mode": "custom_page", "default_site_page_id": page_id},
    )

    factory = async_sessionmaker(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    async with factory() as session:
        state = await load_desired_state(session)

    assert state.default_site is not None
    assert state.default_site.mode == "custom_page"
    assert render_default_site(state)[DEFAULT_SITE_HTML] == CUSTOM_HTML
```

That test needs `pg_conn` in its signature: `async def test_the_default_site_renders_the_referenced_page(client, auth, pg_conn)`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker exec megoopm-test python -m pytest tests/test_nginx_engine.py tests/test_settings_api.py -p no:cacheprovider -p no:warnings`
Expected: FAIL — `apply_config() got an unexpected keyword argument 'default_dir'`, and `state.default_site is None`.

- [ ] **Step 3: Add the config setting**

In `backend/app/core/config.py`, beside `nginx_stream_dir`:

```python
    # Default-site files (a `location` fragment plus an optional HTML document)
    # the base config includes from inside its default_server block. A SIBLING
    # of conf.d, not a child: a child invites a future glob change to sweep
    # these bare `location` blocks into http{}, where they are a syntax error.
    # Defaults to ``{shared_data_dir}/nginx/default``.
    nginx_default_dir: str | None = None
```

and in the `_fill_state_paths` block beside the `nginx_stream_dir` default:

```python
        if self.nginx_default_dir is None:
            self.nginx_default_dir = f"{root}/nginx/default"
```

- [ ] **Step 4: Add the third apply target**

In `backend/app/services/nginx/engine.py`, import `render_default_site` alongside the other renderers, add the parameter:

```python
    default_dir: str | os.PathLike[str] | None = None,
```

extend the docstring with: *"``default_dir`` receives the default-site files. All given directories are written, validated with a single ``nginx -t``, and rolled back together."*

and after the `stream_dir` block:

```python
    if default_dir is not None:
        defaultd = Path(default_dir)
        defaultd.mkdir(parents=True, exist_ok=True)
        targets.append((defaultd, managed_prefix, render_default_site(state)))
```

- [ ] **Step 5: Resolve the setting in the loader**

In `backend/app/services/nginx/loader.py`, add the imports:

```python
from app.models.custom_page import CustomPage
from app.models.enums import DefaultSiteMode
from app.models.instance_settings import InstanceSettings
```

(and `DefaultSiteSpec` to the `state` import block), then:

```python
async def _load_default_site(session: AsyncSession) -> DefaultSiteSpec | None:
    """Read the default-site setting, resolving a referenced page into its HTML.

    The page is dereferenced *here* so the renderer stays a pure function of
    explicit data. ``None`` (no settings row at all) means no file is written
    and nginx falls back to its own no-location-match 404.
    """
    row = await session.get(InstanceSettings, 1)
    if row is None:
        return None

    html = ""
    if row.default_site_mode is DefaultSiteMode.custom_page and row.default_site_page_id:
        page = await session.get(CustomPage, row.default_site_page_id)
        # The FK is RESTRICT, so a missing page means the row was edited outside
        # the API. Render an empty document rather than dropping the whole config.
        html = page.html if page is not None else ""

    return DefaultSiteSpec(
        mode=row.default_site_mode.value,
        redirect_url=row.default_site_redirect_url or "",
        html=html,
    )
```

Call it in `load_desired_state` beside the other loaders and add it to the returned `DesiredState`:

```python
    default_site = await _load_default_site(session)

    return DesiredState(
        proxy_hosts=tuple(host_specs),
        http_upstreams=upstream_specs,
        redirection_hosts=redirection_specs,
        dead_hosts=dead_specs,
        streams=stream_specs,
        stream_upstreams=stream_upstream_specs,
        default_site=default_site,
    )
```

- [ ] **Step 6: Pass the directory from both apply paths**

In `backend/app/tasks/nginx.py`, add `default_dir=settings.nginx_default_dir,` to the `apply_config(...)` call in **both** `_apply_single_host` and `_apply_ha`. Missing one means the default site converges on standalone deployments but not HA ones, or vice versa.

- [ ] **Step 7: Run tests to verify they pass**

Run: `docker exec megoopm-test python -m pytest tests/test_nginx_engine.py tests/test_default_site_render.py tests/test_settings_api.py -p no:cacheprovider -p no:warnings`
Expected: PASS.

- [ ] **Step 8: Change the base nginx config**

In `infra/nginx/nginx.conf`, replace the default server's `location /` block:

```nginx
        location = /healthz {
            access_log off;
            add_header Content-Type text/plain;
            return 200 "ok\n";
        }

        # The default site, chosen under Settings in the app and written here by
        # the backend. There is deliberately no `location /` fallback: nginx
        # answers 404 when no location matches, which is exactly what this block
        # used to hardcode — so an absent file degrades to the old behaviour
        # rather than to something unintended. A second `location /` alongside
        # this include would be a duplicate-location error.
        include /data/nginx/default/*.conf;
```

- [ ] **Step 9: Create the directory at startup**

In **both** `docker-compose.yml` and `docker-compose.ha.yml`, add `/data/nginx/default` to the `data-init` command's `mkdir -p` list, right after `/data/nginx/conf.d/stream`.

- [ ] **Step 10: Verify against a real nginx**

The spec flags this as its one open risk: an `include` with a wildcard that matches nothing is documented as fine, but getting it wrong means the proxy **fails to start** rather than degrading. Confirm rather than trust the docs.

```bash
docker compose build nginx
docker compose up -d
docker compose exec nginx nginx -t                      # empty default dir
curl -i http://localhost/                               # expect 404
curl -fsS http://localhost/healthz                      # expect "ok"
docker compose exec nginx sh -c 'echo "location / { return 444; }" > /data/nginx/default/megoopm-default.conf'
docker compose exec nginx nginx -s reload
curl -i --max-time 5 http://localhost/                  # expect an empty reply
curl -fsS http://localhost/healthz                      # still "ok"
docker compose exec nginx rm /data/nginx/default/megoopm-default.conf
```

If `nginx -t` fails on the empty directory, the fix is to have `data-init` also seed a `megoopm-default.conf` containing `location / { return 404; }` — record whichever way it goes in `docs/nginx-engine.md`.

- [ ] **Step 11: Document the third target**

In `docs/nginx-engine.md`, add the default-site directory to wherever `conf.d` and the stream directory are described: what it holds, that it is included from inside `default_server`, and that an absent file means 404 by nginx's own semantics.

- [ ] **Step 12: Full suite, lint, line endings, commit**

`nginx.conf` is bind-mounted, so a CRLF there reaches a running container.

```bash
docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings
docker exec megoopm-test ruff check app tests
git ls-files --eol infra/nginx/nginx.conf docker-compose.yml docker-compose.ha.yml backend/app/core/config.py backend/app/services/nginx/loader.py backend/app/services/nginx/engine.py backend/app/tasks/nginx.py
git add backend/app/core/config.py backend/app/services/nginx backend/app/tasks/nginx.py backend/tests infra/nginx/nginx.conf docker-compose.yml docker-compose.ha.yml docs/nginx-engine.md
git commit -m "feat(nginx): serve the configured default site for unmatched hosts"
```

---

### Task 6: Custom Pages consequences

**Files:**
- Modify: `backend/app/api/routes/custom_pages.py`
- Test: `backend/tests/test_custom_pages_api.py` (extend)

**Interfaces:**
- Consumes: `InstanceSettings` (Task 1), `after_config_write`.
- Produces: no new public names — behaviour changes only.

`custom_pages.py` currently says writes never enqueue a reload, because "nothing in the rendered configuration references a page yet". Task 5 makes that false. Left alone, editing the page the default site points at would change the database and *not* the served page — until an unrelated edit happened to trigger a reload and the change appeared with no apparent cause. That is worse than not working.

Per **Refinement 2** above, only `PATCH` needs the conditional: `ON DELETE RESTRICT` means a delete that succeeds was of an unreferenced page, which by definition changed no config.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_custom_pages_api.py`:

```python
# --- Coupling to the default site ------------------------------------------


async def _point_default_site_at(client: AsyncClient, auth, page_id: int) -> None:
    resp = await client.patch(
        "/api/v1/settings",
        headers=auth,
        json={"default_site_mode": "custom_page", "default_site_page_id": page_id},
    )
    assert resp.status_code == 200, resp.text


async def test_deleting_a_page_the_default_site_uses_is_refused(
    client: AsyncClient, auth
) -> None:
    """Silently changing what every unmatched visitor sees is worse than a 409."""
    page = await _create(client, auth)
    await _point_default_site_at(client, auth, page["id"])

    resp = await client.delete(f"/api/v1/custom-pages/{page['id']}", headers=auth)
    assert resp.status_code == 409, resp.text
    assert "default site" in resp.text.lower()

    # Still there.
    assert (
        await client.get(f"/api/v1/custom-pages/{page['id']}", headers=auth)
    ).status_code == 200


async def test_deleting_an_unreferenced_page_still_works(client: AsyncClient, auth) -> None:
    page = await _create(client, auth)
    assert (
        await client.delete(f"/api/v1/custom-pages/{page['id']}", headers=auth)
    ).status_code == 204


async def test_editing_an_unreferenced_page_enqueues_no_reload(
    client: AsyncClient, auth, monkeypatch
) -> None:
    page = await _create(client, auth)
    calls = 0

    def _counting_reload() -> TaskEnqueued:
        nonlocal calls
        calls += 1
        return TaskEnqueued(task_id="test-reload-task", status="PENDING")

    monkeypatch.setattr(config_writes, "enqueue_nginx_reload", _counting_reload)

    resp = await client.patch(
        f"/api/v1/custom-pages/{page['id']}", headers=auth, json={"html": "<p>x</p>"}
    )
    assert resp.status_code == 200, resp.text
    assert calls == 0


async def test_editing_the_page_the_default_site_uses_enqueues_one_reload(
    client: AsyncClient, auth, monkeypatch
) -> None:
    """Otherwise the edit lands in the database and never reaches the visitor."""
    page = await _create(client, auth)
    await _point_default_site_at(client, auth, page["id"])

    calls = 0

    def _counting_reload() -> TaskEnqueued:
        nonlocal calls
        calls += 1
        return TaskEnqueued(task_id="test-reload-task", status="PENDING")

    monkeypatch.setattr(config_writes, "enqueue_nginx_reload", _counting_reload)

    resp = await client.patch(
        f"/api/v1/custom-pages/{page['id']}", headers=auth, json={"html": "<p>new</p>"}
    )
    assert resp.status_code == 200, resp.text
    assert calls == 1
    assert resp.headers["X-Config-Reload-Task"] == "test-reload-task"
```

The existing `test_writes_do_not_touch_nginx` asserts zero reloads across create, patch and delete. It stays correct — those pages are unreferenced — but rename it to `test_writes_for_an_unreferenced_page_do_not_touch_nginx` so the *reason* it holds is on the label.

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_custom_pages_api.py -p no:cacheprovider -p no:warnings`
Expected: FAIL — the delete returns 500 (raw `IntegrityError`) and the referenced-page edit enqueues 0 reloads.

- [ ] **Step 3: Update the routes**

In `backend/app/api/routes/custom_pages.py`, replace the module docstring's second paragraph with:

```python
"""...

Most writes here do **not** enqueue an nginx reload: a page nothing references
changes no rendered configuration, so they are audited via
:func:`~app.services.audit.record_audit` directly. The exception is the page the
default site points at — editing that one must converge, or the change would sit
in the database until an unrelated edit happened to trigger a reload. Deleting
that page is refused outright by the ``RESTRICT`` foreign key, so a delete that
succeeds never needs a reload.
"""
```

Add the imports and the helper:

```python
from sqlalchemy.exc import IntegrityError

from app.api.routes._config_writes import after_config_write
from app.models.instance_settings import InstanceSettings


async def _is_default_site(db: SessionDep, page_id: int) -> bool:
    """Whether the default site currently serves this page."""
    row = await db.get(InstanceSettings, 1)
    return row is not None and row.default_site_page_id == page_id
```

In `update_custom_page`, replace the trailing `_audit(...)` call with:

```python
    if await _is_default_site(db, page.id):
        # This page is being served right now; converge it.
        await after_config_write(
            db,
            response,
            actor=admin,
            action=AuditAction.update,
            object_type="custom_page",
            object_id=page.id,
            meta={"changed": sorted(changes), "default_site": True},
        )
    else:
        await _audit(
            db,
            actor=admin.email,
            action=AuditAction.update,
            page_id=page.id,
            changed=sorted(changes),
        )
```

which means `update_custom_page` now takes `response: Response` (import `Response` from `fastapi`).

In `delete_custom_page`, wrap the service call:

```python
    try:
        await custom_page_service.delete_custom_page(db, page_id)
    except custom_page_service.CustomPageNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Custom page not found"
        ) from None
    except IntegrityError:
        # The only FK to custom_pages is instance_settings.default_site_page_id,
        # declared RESTRICT precisely so this delete fails instead of silently
        # changing what every unmatched visitor sees.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This page is in use by the Default site.",
        ) from None
```

`custom_page_service.delete_custom_page` does `db.delete(page)` then `db.commit()` with no `try`/`except`, so the `IntegrityError` propagates to this route unchanged — no service-layer change is needed. The `db.rollback()` above matters: without it the session stays poisoned and the subsequent audit write fails too.

- [ ] **Step 4: Run test to verify it passes**

Run: `docker exec megoopm-test python -m pytest tests/test_custom_pages_api.py -p no:cacheprovider -p no:warnings`
Expected: PASS.

- [ ] **Step 5: Regenerate OpenAPI and run the full suite**

```bash
docker exec megoopm-test python -m scripts.export_openapi
docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings
docker exec megoopm-test ruff check app tests
```

- [ ] **Step 6: Commit**

```bash
git ls-files --eol backend/app/api/routes/custom_pages.py backend/tests/test_custom_pages_api.py backend/openapi.json
git add backend/app/api/routes/custom_pages.py backend/tests/test_custom_pages_api.py backend/openapi.json
git commit -m "fix(custom-pages): converge and protect the page the default site serves"
```

---

### Task 7: Frontend API client and form helpers

**Files:**
- Create: `frontend/src/lib/api/resources/settings.ts`, `frontend/src/components/settings/lib.ts`, `frontend/src/components/settings/lib.test.ts`
- Modify: `frontend/src/lib/api/index.ts`, `frontend/src/lib/api/generated/schema.ts`

**Interfaces:**
- Produces: `instanceSettings.get()` / `.update(body)`; types `InstanceSettings`, `InstanceSettingsUpdate`, `DefaultSiteMode`; `DEFAULT_SITE_MODES` (ordered), `DEFAULT_SITE_MODE_LABELS`, `DEFAULT_SITE_MODE_HINTS`; `SettingsFormState`, `stateFromSettings`, `buildDefaultSitePayload`, `validateSettingsForm`.

- [ ] **Step 1: Regenerate the API types**

```bash
cd frontend && npm run gen:api
git diff --stat src/lib/api/generated/schema.ts
```
Expected: `InstanceSettingsRead` / `InstanceSettingsUpdate` appear.

- [ ] **Step 2: Write the failing test**

Create `frontend/src/components/settings/lib.test.ts`:

```typescript
import { describe, expect, it } from "vitest";

import {
  DEFAULT_SITE_MODES,
  DEFAULT_SITE_MODE_LABELS,
  buildDefaultSitePayload,
  emptyFormState,
  stateFromSettings,
  validateSettingsForm,
  type SettingsFormState,
} from "@/components/settings/lib";
import type { InstanceSettings } from "@/lib/api";

const SETTINGS: InstanceSettings = {
  default_site_mode: "redirect",
  default_site_redirect_url: "https://example.com",
  default_site_page_id: null,
  updated_at: "2026-09-01T00:00:00Z",
};

function state(overrides: Partial<SettingsFormState> = {}): SettingsFormState {
  return { ...emptyFormState(), ...overrides };
}

describe("DEFAULT_SITE_MODES", () => {
  it("lists the five modes in the order the radio group shows them", () => {
    expect(DEFAULT_SITE_MODES).toEqual([
      "congratulations",
      "not_found",
      "no_response",
      "redirect",
      "custom_page",
    ]);
  });

  it("labels every mode", () => {
    for (const mode of DEFAULT_SITE_MODES) {
      expect(DEFAULT_SITE_MODE_LABELS[mode]).toBeTruthy();
    }
  });
});

describe("stateFromSettings", () => {
  it("seeds from the server row", () => {
    expect(stateFromSettings(SETTINGS)).toEqual({
      mode: "redirect",
      redirectUrl: "https://example.com",
      pageId: null,
    });
  });

  it("turns a null url into an empty string so the input stays controlled", () => {
    const seeded = stateFromSettings({ ...SETTINGS, default_site_redirect_url: null });
    expect(seeded.redirectUrl).toBe("");
  });
});

describe("validateSettingsForm", () => {
  it("passes the modes that need nothing else", () => {
    for (const mode of ["congratulations", "not_found", "no_response"] as const) {
      expect(validateSettingsForm(state({ mode }))).toBeNull();
    }
  });

  it("requires a url for redirect", () => {
    expect(validateSettingsForm(state({ mode: "redirect", redirectUrl: "  " }))).toBe(
      "Enter the URL to redirect to.",
    );
  });

  it("requires an absolute http(s) url", () => {
    expect(validateSettingsForm(state({ mode: "redirect", redirectUrl: "example.com" }))).toBe(
      "The URL must start with http:// or https://.",
    );
  });

  it("accepts a valid url", () => {
    expect(
      validateSettingsForm(state({ mode: "redirect", redirectUrl: "https://example.com/x" })),
    ).toBeNull();
  });

  it("requires a page for custom_page", () => {
    expect(validateSettingsForm(state({ mode: "custom_page", pageId: null }))).toBe(
      "Choose a custom page to serve.",
    );
  });
});

describe("buildDefaultSitePayload", () => {
  it("sends only the field the chosen mode uses", () => {
    expect(
      buildDefaultSitePayload(
        state({ mode: "redirect", redirectUrl: "  https://example.com  ", pageId: 4 }),
      ),
    ).toEqual({
      default_site_mode: "redirect",
      default_site_redirect_url: "https://example.com",
      default_site_page_id: null,
    });
  });

  it("nulls both extras for a simple mode", () => {
    expect(
      buildDefaultSitePayload(state({ mode: "not_found", redirectUrl: "https://x.com", pageId: 4 })),
    ).toEqual({
      default_site_mode: "not_found",
      default_site_redirect_url: null,
      default_site_page_id: null,
    });
  });

  it("sends the page for custom_page", () => {
    expect(buildDefaultSitePayload(state({ mode: "custom_page", pageId: 7 }))).toEqual({
      default_site_mode: "custom_page",
      default_site_redirect_url: null,
      default_site_page_id: 7,
    });
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/settings/lib.test.ts`
Expected: FAIL — `Cannot find module '@/components/settings/lib'`.

- [ ] **Step 4: Write the API resource**

Create `frontend/src/lib/api/resources/settings.ts`:

```typescript
/**
 * Typed client for the instance-settings endpoint.
 *
 * One settings row exists, so the path carries no id. `update` requires
 * `default_site_mode`: coherence ("redirect needs a URL") cannot be checked
 * against a payload that omits the mode, so the API asks for the whole
 * default-site group at once — which is also how the UI's single Save works.
 * Shapes come from the generated OpenAPI schema.
 */
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";

export type InstanceSettings = Schemas["InstanceSettingsRead"];
export type InstanceSettingsUpdate = Schemas["InstanceSettingsUpdate"];
export type DefaultSiteMode = Schemas["DefaultSiteMode"];

const BASE = "/api/v1/settings";

export const instanceSettings = {
  get: () => api.get<InstanceSettings>(BASE),
  update: (body: InstanceSettingsUpdate) => api.patch<InstanceSettings>(BASE, body),
} as const;
```

Export from `frontend/src/lib/api/index.ts`, beside the custom-pages exports:

```typescript
export { instanceSettings } from "@/lib/api/resources/settings";
export type {
  InstanceSettings,
  InstanceSettingsUpdate,
  DefaultSiteMode,
} from "@/lib/api/resources/settings";
```

- [ ] **Step 5: Write the helpers**

Create `frontend/src/components/settings/lib.ts`:

```typescript
/**
 * Pure helpers for the Settings page.
 *
 * Kept free of React so the mode branching — which field each mode needs, what
 * gets sent, what is rejected — stays unit-testable without mounting the card.
 *
 * Validation here is a courtesy that catches mistakes before a round trip; the
 * backend is the authority, and its redirect-URL rules are deliberately
 * stricter (it rejects characters that could escape an nginx directive).
 */
import type { DefaultSiteMode, InstanceSettings, InstanceSettingsUpdate } from "@/lib/api";

export { describeError } from "@/components/proxy-hosts/lib";

/** The modes, in the order the radio group shows them. */
export const DEFAULT_SITE_MODES: readonly DefaultSiteMode[] = [
  "congratulations",
  "not_found",
  "no_response",
  "redirect",
  "custom_page",
] as const;

export const DEFAULT_SITE_MODE_LABELS: Record<DefaultSiteMode, string> = {
  congratulations: "Congratulations page",
  not_found: "404 page",
  no_response: "No response (444)",
  redirect: "Redirect",
  custom_page: "Custom page",
};

export const DEFAULT_SITE_MODE_HINTS: Record<DefaultSiteMode, string> = {
  congratulations: "A branded MegooPM page saying the host isn't configured yet.",
  not_found: "A bare 404, with no body. What MegooPM serves today.",
  no_response: "Close the connection without answering. Hides that anything is listening.",
  redirect: "Send the visitor somewhere else with a 301.",
  custom_page: "Serve one of the pages from Custom Pages.",
};

export type SettingsFormState = {
  mode: DefaultSiteMode;
  redirectUrl: string;
  pageId: number | null;
};

export function emptyFormState(): SettingsFormState {
  return { mode: "not_found", redirectUrl: "", pageId: null };
}

export function stateFromSettings(settings: InstanceSettings): SettingsFormState {
  return {
    mode: settings.default_site_mode,
    // Null becomes "" so the input stays controlled across a mode switch.
    redirectUrl: settings.default_site_redirect_url ?? "",
    pageId: settings.default_site_page_id ?? null,
  };
}

/** The first problem blocking a save, or `null` when the form is ready. */
export function validateSettingsForm(state: SettingsFormState): string | null {
  if (state.mode === "redirect") {
    const url = state.redirectUrl.trim();
    if (!url) return "Enter the URL to redirect to.";
    if (!/^https?:\/\//i.test(url)) return "The URL must start with http:// or https://.";
  }
  if (state.mode === "custom_page" && state.pageId === null) {
    return "Choose a custom page to serve.";
  }
  return null;
}

/**
 * Only the field the chosen mode uses is sent; the others are explicitly
 * `null`. The backend clears them anyway, but sending stale values would make
 * the request describe a configuration nobody asked for.
 */
export function buildDefaultSitePayload(state: SettingsFormState): InstanceSettingsUpdate {
  return {
    default_site_mode: state.mode,
    default_site_redirect_url: state.mode === "redirect" ? state.redirectUrl.trim() : null,
    default_site_page_id: state.mode === "custom_page" ? state.pageId : null,
  };
}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/settings/lib.test.ts`
Expected: PASS (all tests).

- [ ] **Step 7: Typecheck, lint, line endings, commit**

```bash
cd frontend && npm run typecheck && npm run lint
git ls-files --eol frontend/src/lib/api/resources/settings.ts frontend/src/lib/api/index.ts frontend/src/components/settings/lib.ts frontend/src/components/settings/lib.test.ts
git add frontend/src/lib/api frontend/src/components/settings
git commit -m "feat(settings): typed client and form helpers for the default site"
```

---

### Task 8: The Settings page

**Files:**
- Create: `frontend/src/components/ui/radio-group.tsx`, `frontend/src/components/settings/settings-view.tsx`, `frontend/src/components/settings/settings-view.test.tsx`
- Modify: `frontend/src/app/(app)/settings/page.tsx`

**Interfaces:**
- Consumes: everything from Task 7; `customPages.list()` from the existing Custom Pages client.
- Produces: `<RadioGroup>`, `<RadioItem value label description />`; `<SettingsView />`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/settings/settings-view.test.tsx`:

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import {
  customPages,
  instanceSettings,
  type CustomPageSummary,
  type InstanceSettings,
} from "@/lib/api";
import { SettingsView } from "@/components/settings/settings-view";

const STAMPS = { created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z" };

function makeSettings(overrides: Partial<InstanceSettings> = {}): InstanceSettings {
  return {
    default_site_mode: "not_found",
    default_site_redirect_url: null,
    default_site_page_id: null,
    updated_at: STAMPS.updated_at,
    ...overrides,
  };
}

const PAGE: CustomPageSummary = {
  id: 5,
  name: "Access denied",
  description: "",
  size_bytes: 120,
  ...STAMPS,
};

describe("SettingsView", () => {
  beforeEach(() => {
    push.mockClear();
    vi.spyOn(instanceSettings, "get").mockResolvedValue(makeSettings());
    vi.spyOn(instanceSettings, "update").mockResolvedValue(makeSettings());
    vi.spyOn(customPages, "list").mockResolvedValue([PAGE]);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("offers all five modes with the saved one selected", async () => {
    render(<SettingsView />);
    expect(await screen.findByRole("radio", { name: /404 page/i })).toBeChecked();
    for (const name of [
      /Congratulations page/i,
      /404 page/i,
      /No response/i,
      /Redirect/i,
      /Custom page/i,
    ]) {
      expect(screen.getByRole("radio", { name })).toBeInTheDocument();
    }
  });

  it("reveals the URL field only for Redirect", async () => {
    const user = userEvent.setup();
    render(<SettingsView />);
    await screen.findByRole("radio", { name: /404 page/i });
    expect(screen.queryByLabelText("Redirect to")).not.toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: /Redirect/i }));
    expect(screen.getByLabelText("Redirect to")).toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: /No response/i }));
    expect(screen.queryByLabelText("Redirect to")).not.toBeInTheDocument();
  });

  it("reveals the page picker only for Custom page", async () => {
    const user = userEvent.setup();
    render(<SettingsView />);
    await screen.findByRole("radio", { name: /404 page/i });
    expect(screen.queryByLabelText("Custom page")).not.toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: /Custom page/i }));
    expect(await screen.findByLabelText("Custom page")).toBeInTheDocument();
  });

  it("saves a simple mode", async () => {
    const user = userEvent.setup();
    render(<SettingsView />);
    await user.click(await screen.findByRole("radio", { name: /No response/i }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(instanceSettings.update).toHaveBeenCalledTimes(1));
    expect(instanceSettings.update).toHaveBeenCalledWith({
      default_site_mode: "no_response",
      default_site_redirect_url: null,
      default_site_page_id: null,
    });
  });

  it("saves a redirect with its URL", async () => {
    const user = userEvent.setup();
    render(<SettingsView />);
    await user.click(await screen.findByRole("radio", { name: /Redirect/i }));
    await user.type(screen.getByLabelText("Redirect to"), "https://example.com");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(instanceSettings.update).toHaveBeenCalledTimes(1));
    expect(vi.mocked(instanceSettings.update).mock.calls[0][0]).toMatchObject({
      default_site_mode: "redirect",
      default_site_redirect_url: "https://example.com",
    });
  });

  it("blocks a redirect with no URL and says why", async () => {
    const user = userEvent.setup();
    render(<SettingsView />);
    await user.click(await screen.findByRole("radio", { name: /Redirect/i }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Enter the URL to redirect to.");
    expect(instanceSettings.update).not.toHaveBeenCalled();
  });

  it("points at Custom Pages when there are none to choose", async () => {
    vi.mocked(customPages.list).mockResolvedValue([]);
    const user = userEvent.setup();
    render(<SettingsView />);
    await user.click(await screen.findByRole("radio", { name: /Custom page/i }));

    expect(await screen.findByText(/no custom pages/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Create a page/i })).toBeInTheDocument();
    expect(screen.queryByLabelText("Custom page")).not.toBeInTheDocument();
  });

  it("keeps Save disabled until something changes", async () => {
    const user = userEvent.setup();
    render(<SettingsView />);
    await screen.findByRole("radio", { name: /404 page/i });
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    await user.click(screen.getByRole("radio", { name: /Redirect/i }));
    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
  });

  it("surfaces a load failure with a retry", async () => {
    vi.mocked(instanceSettings.get).mockRejectedValueOnce(new Error("boom"));
    render(<SettingsView />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn't load/i);
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/settings/settings-view.test.tsx`
Expected: FAIL — `Cannot find module '@/components/settings/settings-view'`.

- [ ] **Step 3: Write the radio primitive**

Create `frontend/src/components/ui/radio-group.tsx`, following the `switch.tsx` idiom (tokens only, no hardcoded colours):

```tsx
"use client"

import { Radio as RadioPrimitive } from "@base-ui/react/radio"
import { RadioGroup as RadioGroupPrimitive } from "@base-ui/react/radio-group"

import { cn } from "@/lib/utils"

function RadioGroup({ className, ...props }: RadioGroupPrimitive.Props) {
  return (
    <RadioGroupPrimitive
      data-slot="radio-group"
      className={cn("flex flex-col gap-3", className)}
      {...props}
    />
  )
}

function Radio({ className, ...props }: RadioPrimitive.Root.Props) {
  return (
    <RadioPrimitive.Root
      data-slot="radio"
      className={cn(
        "flex size-4 shrink-0 items-center justify-center rounded-full border border-input bg-background outline-none transition-colors data-checked:border-primary data-checked:bg-primary focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    >
      <RadioPrimitive.Indicator
        data-slot="radio-indicator"
        className="size-1.5 rounded-full bg-primary-foreground data-unchecked:hidden"
      />
    </RadioPrimitive.Root>
  )
}

export { Radio, RadioGroup }
```

- [ ] **Step 4: Write the view**

Create `frontend/src/components/settings/settings-view.tsx`:

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Settings as SettingsIcon } from "lucide-react";
import { toast } from "sonner";

import {
  customPages,
  instanceSettings,
  type CustomPageSummary,
  type DefaultSiteMode,
} from "@/lib/api";
import {
  DEFAULT_SITE_MODES,
  DEFAULT_SITE_MODE_HINTS,
  DEFAULT_SITE_MODE_LABELS,
  buildDefaultSitePayload,
  describeError,
  emptyFormState,
  stateFromSettings,
  validateSettingsForm,
  type SettingsFormState,
} from "@/components/settings/lib";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Radio, RadioGroup } from "@/components/ui/radio-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * Instance configuration. Today it holds one card: the default site — what
 * nginx returns for a request matching no configured host.
 */
export function SettingsView() {
  const router = useRouter();
  const [form, setForm] = useState<SettingsFormState>(emptyFormState);
  const [saved, setSaved] = useState<SettingsFormState>(emptyFormState);
  const [pages, setPages] = useState<CustomPageSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // Both are needed to render the card: the picker's options are as much
      // part of the form as the setting itself.
      const [settings, list] = await Promise.all([
        instanceSettings.get(),
        customPages.list(),
      ]);
      setForm(stateFromSettings(settings));
      setSaved(stateFromSettings(settings));
      setPages(list);
      setLoadError(null);
    } catch (err) {
      setLoadError(describeError(err).message);
    } finally {
      setLoading(false);
    }
  }, []);

  // The IIFE keeps the effect callback itself synchronous; `load` awaits before
  // any setState, so nothing updates state synchronously in the effect body.
  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load]);

  const dirty =
    form.mode !== saved.mode ||
    form.redirectUrl !== saved.redirectUrl ||
    form.pageId !== saved.pageId;

  async function handleSave() {
    const problem = validateSettingsForm(form);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const updated = await instanceSettings.update(buildDefaultSitePayload(form));
      setSaved(stateFromSettings(updated));
      setForm(stateFromSettings(updated));
      toast.success("Default site saved");
    } catch (err) {
      // 422 → the backend's stricter URL rules, or an unknown page id.
      const described = describeError(err);
      setError(described.message);
      toast.error(described.message);
    } finally {
      setSaving(false);
    }
  }

  if (loadError) {
    return (
      <div className="mx-auto flex max-w-3xl flex-col items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
        <p className="text-sm text-destructive" role="alert">
          Couldn&apos;t load settings: {loadError}
        </p>
        <Button variant="outline" size="sm" onClick={() => void load()}>
          Retry
        </Button>
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div className="flex items-center gap-3">
        <div className="flex size-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          <SettingsIcon className="size-5" />
        </div>
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Settings</h2>
          <p className="text-sm text-muted-foreground">Instance configuration.</p>
        </div>
      </div>

      <section className="space-y-4 rounded-xl border p-5">
        <div>
          <h3 className="text-sm font-semibold">Default site</h3>
          <p className="text-sm text-muted-foreground">
            What to serve for a request that matches no configured host.
          </p>
        </div>

        {loading ? (
          <div className="space-y-3">
            <Skeleton className="h-5 w-2/3" />
            <Skeleton className="h-5 w-1/2" />
            <Skeleton className="h-5 w-3/5" />
          </div>
        ) : (
          <>
            <RadioGroup
              value={form.mode}
              // base-ui passes (value, eventDetails) — the second argument is
              // ignored here but must not be mistaken for the value.
              onValueChange={(value) =>
                setForm((current) => ({ ...current, mode: value as DefaultSiteMode }))
              }
            >
              {DEFAULT_SITE_MODES.map((mode) => (
                <label key={mode} className="flex items-start gap-2.5">
                  <Radio
                    value={mode}
                    aria-label={DEFAULT_SITE_MODE_LABELS[mode]}
                    disabled={saving}
                    className="mt-0.5"
                  />
                  <span className="space-y-0.5">
                    <span className="block text-sm font-medium leading-none">
                      {DEFAULT_SITE_MODE_LABELS[mode]}
                    </span>
                    <span className="block text-xs text-muted-foreground">
                      {DEFAULT_SITE_MODE_HINTS[mode]}
                    </span>
                  </span>
                </label>
              ))}
            </RadioGroup>

            {form.mode === "redirect" ? (
              <div className="space-y-1.5">
                <Label htmlFor="ds-url">Redirect to</Label>
                <Input
                  id="ds-url"
                  value={form.redirectUrl}
                  onChange={(e) =>
                    setForm((current) => ({ ...current, redirectUrl: e.target.value }))
                  }
                  placeholder="https://example.com"
                  disabled={saving}
                />
              </div>
            ) : null}

            {form.mode === "custom_page" ? (
              pages.length === 0 ? (
                <div className="flex flex-col items-start gap-2 rounded-lg border border-dashed p-4">
                  <p className="text-sm text-muted-foreground">
                    You have no custom pages yet.
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => router.push("/custom-pages/new")}
                  >
                    Create a page
                  </Button>
                </div>
              ) : (
                <div className="space-y-1.5">
                  <Label htmlFor="ds-page">Custom page</Label>
                  <Select
                    value={form.pageId === null ? "" : String(form.pageId)}
                    onValueChange={(value) =>
                      setForm((current) => ({ ...current, pageId: Number(value) }))
                    }
                  >
                    <SelectTrigger id="ds-page" disabled={saving}>
                      <SelectValue placeholder="Choose a page" />
                    </SelectTrigger>
                    <SelectContent>
                      {pages.map((page) => (
                        <SelectItem key={page.id} value={String(page.id)}>
                          {page.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )
            ) : null}

            {error ? (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            ) : null}

            <div className="flex justify-end">
              <Button onClick={handleSave} disabled={saving || !dirty}>
                {saving ? "Saving…" : "Save changes"}
              </Button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
```

- [ ] **Step 5: Mount it**

Replace `frontend/src/app/(app)/settings/page.tsx`:

```tsx
import type { Metadata } from "next";

import { SettingsView } from "@/components/settings/settings-view";

export const metadata: Metadata = { title: "Settings" };

export default function SettingsPage() {
  return <SettingsView />;
}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/settings/`
Expected: PASS.

If a radio's accessible name does not resolve, base-ui may compose it differently from the `aria-label` above — inspect with `screen.debug()` and adjust the component (not the test's intent) so each radio is reachable by its visible label.

- [ ] **Step 7: Full frontend verification**

```bash
cd frontend && npx vitest run && npm run typecheck && npm run lint && npm run build
```
Expected: all pass; the build lists `/settings` as before.

- [ ] **Step 8: Check it in the real app**

```bash
docker compose up -d --build
```

Open Settings, pick each mode in turn, and after each save hit the proxy by IP:

- Congratulations → the themed page; toggle your OS theme and confirm both schemes.
- 404 page → a bare 404.
- No response → connection closed, empty reply.
- Redirect → a 301 to the URL.
- Custom page → the chosen page's HTML.

Then edit that custom page and reload the proxy URL: the change must appear **without** touching anything else. That is the Task 6 conditional reload; if it does not appear, that is the bug Task 6 exists to prevent.

Finally, try deleting the page while it is selected — expect a 409 and a message naming the Default site.

- [ ] **Step 9: Line endings and commit**

```bash
git ls-files --eol frontend/src/components/ui/radio-group.tsx frontend/src/components/settings/settings-view.tsx frontend/src/components/settings/settings-view.test.tsx "frontend/src/app/(app)/settings/page.tsx"
git add frontend/src/components/ui/radio-group.tsx frontend/src/components/settings "frontend/src/app/(app)/settings/page.tsx"
git commit -m "feat(settings): choose the default site from the Settings page"
```

---

## Done when

- Every task's steps are checked off.
- `docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings` — all pass, no new skips.
- `docker exec megoopm-test ruff check app tests alembic` — clean.
- `cd frontend && npx vitest run && npm run typecheck && npm run lint && npm run build` — all pass.
- `git ls-files --eol` shows no `w/crlf` on any changed file.
- The manual matrix in Task 8 Step 8 has been walked, including the edit-the-referenced-page case and the delete-while-referenced 409.
- Test containers torn down: `docker rm -f megoopm-test megoopm-testdb && docker network rm megoopm-testnet`.
