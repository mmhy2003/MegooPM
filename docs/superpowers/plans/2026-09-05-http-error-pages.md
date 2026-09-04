# Branded HTTP Error Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every error nginx itself produces is answered by a MegooPM-branded page, on every domain the instance serves, and an operator can swap any of the eight for a custom page.

**Architecture:** An `error_page` table storing only what the operator changed. The renderer writes eight documents plus one `error_page`/`internal location` fragment into the shared default directory it already owns; each of the four server templates includes that fragment once per `server {}` block. The palette moves into a shared partial so the two existing branded pages and the new one cannot drift.

**Tech Stack:** Everything already in the repo: SQLAlchemy, Alembic, Jinja2 with `StrictUndefined`, FastAPI, Next.js with base-ui. `sharp` (already installed in the frontend) generates the logo asset once.

**Spec:** `docs/superpowers/specs/2026-09-05-http-error-pages-design.md`

## Global Constraints

- **Eight codes, fixed:** 400, 401, 403, 404, 500, 502, 503, 504. A ninth is a 422.
- **No `proxy_intercept_errors`.** Only errors nginx itself generates are branded; a backend's own error responses pass through untouched.
- **A code with no row means the branded default.** Setting a code back to default deletes its row; a fresh install seeds nothing.
- **All eight documents are always written**, so an `error_page` directive can never point at a missing file.
- **No external requests from any rendered page.** No webfont link, no CDN, no `<img src="http...">`. The logo is an inline base64 `data:` URI.
- **No request data on an error page**: no host name, no upstream address, no path. Same rule `banned.html.j2` already states.
- **The fragment's extension is `.inc`, never `.conf`** — the base config's `include .../*.conf` would otherwise parse it at the top level.
- **Every `location` serving an error document is `internal`.**
- **Copy is fixed** (spec's table): 400 "Bad request" / "The server couldn't understand that request."; 401 "Authentication required" / "This area needs credentials."; 403 "Access denied" / "You don't have permission to view this."; 404 "Not found" / "There's nothing at this address."; 500 "Something went wrong" / "The site hit an unexpected error."; 502 "Bad gateway" / "The site behind this address didn't respond correctly."; 503 "Service unavailable" / "The site is temporarily unable to handle the request."; 504 "Gateway timeout" / "The site behind this address took too long to answer."
- **Every `oklch()` is preceded by a hex fallback**, as the existing templates do.
- **Backend tests run in the container recipe** (Task 1, Step 2). Frontend commands run from `frontend/`. Format frontend files with `npx prettier --write --print-width 100 <files>` only. **Commit in a separate call after reading the test result** — a `cmd | grep … && git commit` chain commits even when `cmd` failed.
- **After any schema change:** `docker exec megoopm-test python -m scripts.export_openapi`, then `cd frontend && npm run gen:api`.

## File Structure

**Backend**

| file | responsibility |
| --- | --- |
| `app/models/enums.py` | `ErrorPageMode` |
| `app/models/error_page.py` | the table |
| `alembic/versions/0032_error_pages.py` | migration |
| `app/services/error_page.py` | read the effective set; write the whole set |
| `app/services/nginx/state.py` | `ErrorPageSpec`, `DesiredState.error_pages` |
| `app/services/nginx/loader.py` | rows → specs, documents dereferenced |
| `app/services/nginx/renderer.py` | eight documents + the fragment |
| `app/templates/nginx/error.html.j2` | the branded page |
| `app/templates/nginx/_palette.css.j2` | the shared palette, included by three pages |
| `app/templates/nginx/errors.conf.inc.j2` | the fragment |
| `app/templates/nginx/assets/logo.png` | the inlined logo |
| `app/templates/nginx/{congratulations,banned}.html.j2` | logo + shared palette |
| `app/templates/nginx/{server,redirect,dead,default_tls}.conf.j2` | the include line |
| `app/schemas/error_page.py` | `ErrorPageRead`, `ErrorPageUpdate` |
| `app/api/routes/settings.py` | `GET`/`PUT /settings/error-pages` |

**Frontend**

| file | responsibility |
| --- | --- |
| `src/lib/api/resources/settings.ts` | `listErrorPages`, `updateErrorPages`, types |
| `src/components/settings/error-pages-card.tsx` | the eight rows |
| `src/components/settings/settings-view.tsx` | mount |

---

### Task 1: Storage

**Files:**
- Modify: `backend/app/models/enums.py`, `backend/app/models/__init__.py`, `backend/tests/conftest.py`
- Create: `backend/app/models/error_page.py`, `backend/alembic/versions/0032_error_pages.py`
- Test: `backend/tests/test_error_page_model.py`, `backend/tests/test_error_pages_migration.py`

**Interfaces:**
- Produces:
  - `ErrorPageMode(default, custom_page)` in `app.models.enums`
  - `ErrorPage(code: int PK, mode: ErrorPageMode, custom_page_id: int | None, created_at, updated_at)` in `app.models.error_page`
  - `ERROR_CODES: tuple[int, ...] = (400, 401, 403, 404, 500, 502, 503, 504)` in `app.models.error_page`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_error_page_model.py`:

```python
"""Structural checks for the error_page mapping (no database)."""

from __future__ import annotations

from app.models.error_page import ERROR_CODES, ErrorPage


def test_the_eight_codes_are_fixed() -> None:
    # The set is closed: the UI renders exactly these rows and the renderer
    # writes exactly these files, so a ninth code would be invisible in both.
    assert ERROR_CODES == (400, 401, 403, 404, 500, 502, 503, 504)


def test_table_shape() -> None:
    table = ErrorPage.__table__
    assert table.name == "error_page"
    assert {c.name for c in table.columns} == {
        "code",
        "mode",
        "custom_page_id",
        "created_at",
        "updated_at",
    }
    # The code is the identity: one row per code, at most.
    assert [c.name for c in table.primary_key.columns] == ["code"]
    assert table.c.mode.nullable is False
    assert set(table.c.mode.type.enums) == {"default", "custom_page"}


def test_the_page_reference_is_restrict() -> None:
    # A page an error binding uses cannot be deleted out from under it, the
    # same rule the default site and the ban page already follow.
    fks = {fk.column.table.name: fk.ondelete for fk in ErrorPage.__table__.foreign_keys}
    assert fks == {"custom_pages": "RESTRICT"}
```

Create `backend/tests/test_error_pages_migration.py`:

```python
"""The 0032 table and its constraint, against a real Postgres.

Mirrors tests/test_location_targets_migration.py: Alembic drives an async
engine off ``settings.database_url``, so the run is pointed at a throwaway
schema by setting the search path on the role — asyncpg ignores PGOPTIONS,
and a URL query would have to survive ConfigParser's '%' interpolation.
"""

from __future__ import annotations

import asyncio

import pytest
from alembic import command
from alembic.config import Config
from app.core.config import settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

SCHEMA = "error_page_probe"
_BASE_URL = settings.database_url


async def _exec(statements: list[str]) -> list[tuple]:
    engine = create_async_engine(_BASE_URL, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'SET search_path TO "{SCHEMA}"'))
            result = None
            for sql in statements:
                result = await conn.execute(text(sql))
            return list(result.all()) if result is not None and result.returns_rows else []
    finally:
        await engine.dispose()


async def _set_role_search_path(schema: str) -> None:
    engine = create_async_engine(_BASE_URL, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            user = (await conn.execute(text("SELECT current_user"))).scalar_one()
            await conn.execute(text(f'ALTER ROLE "{user}" SET search_path TO {schema}'))
    finally:
        await engine.dispose()


async def _reset_schema() -> None:
    engine = create_async_engine(_BASE_URL, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SET search_path TO public"))
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))
            await conn.execute(text(f'CREATE SCHEMA "{SCHEMA}"'))
    finally:
        await engine.dispose()


@pytest.fixture
def migrated():
    try:
        asyncio.run(_reset_schema())
    except Exception:  # pragma: no cover - environment without a database
        pytest.skip("No database reachable at DATABASE_URL")
    asyncio.run(_set_role_search_path(SCHEMA))
    cfg = Config("alembic.ini")
    yield lambda revision: command.upgrade(cfg, revision)
    asyncio.run(_set_role_search_path("public"))
    asyncio.run(_reset_schema())


def test_the_table_starts_empty(migrated) -> None:
    # A fresh install seeds nothing: an absent row *is* the branded default.
    migrated("0032_error_pages")
    rows = asyncio.run(_exec(["SELECT count(*) FROM error_page"]))
    assert rows[0][0] == 0


def test_the_constraint_ties_the_page_to_the_mode(migrated) -> None:
    migrated("0032_error_pages")
    asyncio.run(
        _exec(["INSERT INTO custom_pages (id, name, description, html) VALUES (1, 'p', '', '')"])
    )

    # default + a page, and custom_page without one, are both nonsense.
    for sql in (
        "INSERT INTO error_page (code, mode, custom_page_id) VALUES (404, 'default', 1)",
        "INSERT INTO error_page (code, mode, custom_page_id) VALUES (404, 'custom_page', NULL)",
    ):
        with pytest.raises(Exception, match="error_page_mode_needs_page"):
            asyncio.run(_exec([sql]))

    asyncio.run(
        _exec(
            [
                "INSERT INTO error_page (code, mode, custom_page_id) VALUES (404, 'custom_page', 1)",
                "INSERT INTO error_page (code, mode, custom_page_id) VALUES (502, 'default', NULL)",
            ]
        )
    )
    rows = asyncio.run(_exec(["SELECT code, mode FROM error_page ORDER BY code"]))
    assert [tuple(r) for r in rows] == [(404, "custom_page"), (502, "default")]
```

- [ ] **Step 2: Start the test stack and run them to verify they fail**

```bash
export MSYS_NO_PATHCONV=1
docker network create megoopm-testnet 2>/dev/null || true
docker run -d --name megoopm-testdb --network megoopm-testnet \
  -e POSTGRES_USER=megoopm -e POSTGRES_PASSWORD=megoopm -e POSTGRES_DB=megoopm postgres:16-alpine
docker run -d --name megoopm-test --user root --network megoopm-testnet \
  -v "C:/Projects/megoopm/backend:/src" -w /src \
  -e CELERY_TASK_ALWAYS_EAGER=true -e CELERY_RESULT_BACKEND=cache+memory:// \
  -e DATABASE_URL="postgresql+asyncpg://megoopm:megoopm@megoopm-testdb:5432/megoopm" \
  --entrypoint sleep megoopm-backend infinity
docker exec megoopm-test pip install -q "pytest>=8.2" "pytest-asyncio>=0.23" \
  "aiosqlite>=0.20" "ruff>=0.6" "maxminddb" "webauthn>=3.0" "cbor2>=5.6"
docker exec megoopm-test python -m pytest tests/test_error_page_model.py tests/test_error_pages_migration.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.error_page'`.

> **If the migration test leaves the database dirty** (a failed run can migrate
> the default schema): reset it before trusting any later suite —
> `docker exec megoopm-testdb psql -U megoopm -d megoopm -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO megoopm;"`

- [ ] **Step 3: The enum and the model**

In `backend/app/models/enums.py`, after `LocationTarget`:

```python
class ErrorPageMode(enum.StrEnum):
    """What one HTTP status code is answered with."""

    default = "default"
    custom_page = "custom_page"
```

Add `"ErrorPageMode"` to `__all__`.

Create `backend/app/models/error_page.py`:

```python
"""What each common HTTP error status is answered with.

One row per *configured* code. A code with no row is served the branded
default, so a fresh install seeds nothing and this table only ever holds what
an operator changed — setting a code back to the default deletes its row.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, Enum, ForeignKey, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.custom_page import CustomPage
from app.models.enums import ErrorPageMode
from app.models.mixins import TimestampMixin

#: The codes the settings page offers and the renderer writes. Closed on
#: purpose: the UI renders exactly these rows, so a ninth would be invisible.
ERROR_CODES: tuple[int, ...] = (400, 401, 403, 404, 500, 502, 503, 504)


class ErrorPage(TimestampMixin, Base):
    __tablename__ = "error_page"
    __table_args__ = (
        CheckConstraint(
            "(mode = 'custom_page' AND custom_page_id IS NOT NULL)"
            " OR (mode = 'default' AND custom_page_id IS NULL)",
            name="error_page_mode_needs_page",
        ),
    )

    # The status code is the identity: at most one binding per code.
    code: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    mode: Mapped[ErrorPageMode] = mapped_column(
        Enum(
            ErrorPageMode,
            name="error_page_mode",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    # RESTRICT, like the default site's: a page in use cannot be deleted.
    custom_page_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("custom_pages.id", ondelete="RESTRICT"), nullable=True, index=True
    )

    custom_page: Mapped[CustomPage] = relationship()


__all__ = ["ERROR_CODES", "ErrorPage"]
```

Register it in `backend/app/models/__init__.py` (import after `app.models.enums`'s
neighbours, alphabetically among the model imports; `"ErrorPage"` in `__all__`)
and add `ErrorPage.__table__` to the `tables=[…]` list in
`backend/tests/conftest.py` with its import.

- [ ] **Step 4: The migration**

Create `backend/alembic/versions/0032_error_pages.py`:

```python
"""Branded HTTP error pages: what each common status is answered with

One row per configured code; an absent row means the shipped page. Purely
additive — nothing existing changes, and a downgrade only drops what this
created.

Revision ID: 0032_error_pages
Revises: 0031_location_targets
Create Date: 2026-09-05 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0032_error_pages"
down_revision: str | None = "0031_location_targets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MODE = sa.Enum("default", "custom_page", name="error_page_mode")


def upgrade() -> None:
    op.create_table(
        "error_page",
        sa.Column("code", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("mode", _MODE, nullable=False),
        sa.Column("custom_page_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(mode = 'custom_page' AND custom_page_id IS NOT NULL)"
            " OR (mode = 'default' AND custom_page_id IS NULL)",
            name="error_page_mode_needs_page",
        ),
        sa.ForeignKeyConstraint(
            ["custom_page_id"],
            ["custom_pages.id"],
            name=op.f("fk_error_page_custom_page_id_custom_pages"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_error_page")),
    )
    op.create_index(op.f("ix_error_page_custom_page_id"), "error_page", ["custom_page_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_error_page_custom_page_id"), table_name="error_page")
    op.drop_table("error_page")
    _MODE.drop(op.get_bind(), checkfirst=True)
```

`create_table` emits `CREATE TYPE` for the enum by itself, so unlike
`0030`/`0031` this migration does not create it by hand — but it must still
drop it by hand.

- [ ] **Step 5: Run the tests to verify they pass, lint, commit**

```bash
docker exec megoopm-test ruff format app/models/enums.py app/models/error_page.py alembic/versions/0032_error_pages.py tests/test_error_page_model.py tests/test_error_pages_migration.py
docker exec megoopm-test python -m pytest tests/test_error_page_model.py tests/test_error_pages_migration.py tests/test_settings_api.py -p no:cacheprovider -p no:warnings
docker exec megoopm-test ruff check app tests
```
Then, in a separate call after reading the result:
```bash
git add backend/app/models backend/tests/conftest.py backend/alembic/versions/0032_error_pages.py backend/tests/test_error_page_model.py backend/tests/test_error_pages_migration.py
git commit -m "feat(error-pages): one row per configured status code

An absent row is the branded default, so a fresh install seeds nothing and
the table only holds what an operator changed.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: The logo asset and the shared palette

The two existing branded pages get the logo and stop carrying their own copy
of the palette, so the third page cannot drift from them.

**Files:**
- Create: `frontend/scripts/generate-nginx-logo.mjs`, `backend/app/templates/nginx/assets/logo.png`, `backend/app/templates/nginx/_palette.css.j2`
- Modify: `backend/app/services/nginx/renderer.py`, `backend/app/templates/nginx/congratulations.html.j2`, `backend/app/templates/nginx/banned.html.j2`
- Test: `backend/tests/test_branded_pages.py`

**Interfaces:**
- Produces: `renderer.LOGO_DATA_URI: str` — the inlined logo, exposed to Jinja as the global `logo_data_uri`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_branded_pages.py`:

```python
"""What every MegooPM-branded page must be true of.

These pages are served when something is already wrong — no host matched, a
backend is down, a visitor is banned — so the rules are about surviving that
moment, not about looks.
"""

from __future__ import annotations

import re

import pytest
from app.services.nginx.renderer import _env

PAGES = ["congratulations.html.j2", "banned.html.j2"]


def _render(name: str) -> str:
    return _env().get_template(name).render()


@pytest.mark.parametrize("name", PAGES)
def test_a_page_makes_no_external_request(name: str) -> None:
    # The moment this page renders is the moment the network is least likely
    # to work. A webfont link or a CDN image would degrade exactly then.
    html = _render(name)
    assert "http://" not in html
    assert "https://" not in html
    assert "//fonts." not in html


@pytest.mark.parametrize("name", PAGES)
def test_a_page_carries_the_logo_inline(name: str) -> None:
    html = _render(name)
    assert "data:image/png;base64," in html
    # The wordmark alone was what these had before; the logo replaces it.
    assert "<img" in html


@pytest.mark.parametrize("name", PAGES)
def test_every_oklch_has_a_hex_fallback(name: str) -> None:
    # Chrome 111 / Safari 15.4 / Firefox 113 (2023) and older take the hex.
    # Declaration order is what makes that work, so check pairs, not counts.
    html = _render(name)
    for match in re.finditer(r"(--[\w-]+):\s*oklch\(", html):
        prop = match.group(1)
        before = html[: match.start()]
        assert re.search(rf"{prop}:\s*#[0-9a-fA-F]{{3,8}};\s*$", before.rstrip("\n") + "\n", re.M), (
            f"{prop} in {name} has no hex fallback immediately before its oklch()"
        )


@pytest.mark.parametrize("name", PAGES)
def test_a_page_has_a_dark_treatment(name: str) -> None:
    assert "prefers-color-scheme: dark" in _render(name)


def test_the_pages_share_one_palette() -> None:
    # Three files each carrying their own copy is three places to change a
    # colour and two chances to forget one.
    source = (_env().loader.get_source(_env(), "congratulations.html.j2"))[0]
    assert '{% include "_palette.css.j2" %}' in source
    banned = (_env().loader.get_source(_env(), "banned.html.j2"))[0]
    assert '{% include "_palette.css.j2" %}' in banned
```

- [ ] **Step 2: Run them to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_branded_pages.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — no `data:image/png;base64,` and no `_palette.css.j2` include.

- [ ] **Step 3: Generate the logo**

Create `frontend/scripts/generate-nginx-logo.mjs`:

```js
/**
 * Generate the logo the nginx-served pages inline.
 *
 * Run by hand after the logo changes; the output is committed, so a deploy
 * never depends on this script or on `sharp`:
 *
 *   node scripts/generate-nginx-logo.mjs
 *
 * 64px, because it is embedded as base64 into *nine* documents (eight error
 * pages, the default site, the ban page) — the 512px original would add a
 * quarter megabyte to the config directory on every apply.
 */
import path from "node:path";
import { fileURLToPath } from "node:url";

import sharp from "sharp";

const here = path.dirname(fileURLToPath(import.meta.url));
const SOURCE = path.join(here, "..", "public", "logo.png");
const OUT = path.join(here, "..", "..", "backend", "app", "templates", "nginx", "assets", "logo.png");
const SIZE = 64;

await sharp(SOURCE)
  .resize(SIZE, SIZE, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
  .png({ compressionLevel: 9 })
  .toFile(OUT);

console.log(`Wrote ${OUT}`);
```

Run it and check the size:

```bash
cd frontend && mkdir -p ../backend/app/templates/nginx/assets && node scripts/generate-nginx-logo.mjs
ls -l ../backend/app/templates/nginx/assets/logo.png
```
Expected: a file under 10 KB. If it is larger, drop `SIZE` to 48 and re-run —
the base64 form is about a third larger again, and it is embedded nine times.

- [ ] **Step 4: Expose it to Jinja**

In `backend/app/services/nginx/renderer.py`, above `_env()`:

```python
#: The logo, inlined into every branded page as a data URI.
#:
#: Read once at import: these templates render on every apply, and the file
#: never changes at runtime. Inline rather than linked because a branded page
#: renders when something is already broken — a second request is exactly what
#: cannot be relied on then.
_LOGO_PATH = TEMPLATES_DIR / "assets" / "logo.png"
LOGO_DATA_URI = "data:image/png;base64," + base64.b64encode(_LOGO_PATH.read_bytes()).decode()
```

(Add `import base64` at the top.) Then, inside `_env()`, after the
`Environment(...)` is built, register it as a global — the function currently
returns the environment directly, so bind it first:

```python
def _env() -> Environment:
    """Build the Jinja environment once and cache it."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        undefined=StrictUndefined,  # fail loudly on a typo'd template variable
        keep_trailing_newline=True,
        trim_blocks=False,
        lstrip_blocks=False,
    )
    # A global, not a per-render argument: every branded page wants it and
    # StrictUndefined would turn a forgotten argument into a 500 at apply time.
    env.globals["logo_data_uri"] = LOGO_DATA_URI
    return env
```

- [ ] **Step 5: Extract the palette**

Create `backend/app/templates/nginx/_palette.css.j2` with the palette
**exactly as it stands in `congratulations.html.j2` today** — both the
`:root` block and the `@media (prefers-color-scheme: dark)` block, comments
included. Copy it verbatim; this step must not change a single colour.

Then in both `congratulations.html.j2` and `banned.html.j2`, replace their
palette blocks with:

```jinja
{% include "_palette.css.j2" %}
```

and replace the text wordmark

```html
<p class="wordmark">MegooPM</p>
```

with

```html
<img class="logo" src="{{ logo_data_uri }}" alt="MegooPM" width="64" height="64" />
```

adding, in each page's own `<style>`:

```css
.logo {
  display: block;
  width: 64px;
  height: 64px;
  margin: 0 auto 1rem;
  filter: var(--logo-glow, none);
}
```

and in `_palette.css.j2`'s dark block, alongside `--glow`:

```css
--logo-glow: drop-shadow(0 0 10px rgba(79, 220, 239, 0.45));
```

- [ ] **Step 6: Run the tests to verify they pass, lint, commit**

```bash
docker exec megoopm-test ruff format app/services/nginx/renderer.py tests/test_branded_pages.py
docker exec megoopm-test python -m pytest tests/test_branded_pages.py tests/test_default_site_render.py tests/test_nginx_render.py -p no:cacheprovider -p no:warnings
docker exec megoopm-test ruff check app tests
```
Commit separately:
```bash
git add backend/app/services/nginx/renderer.py backend/app/templates/nginx frontend/scripts/generate-nginx-logo.mjs backend/tests/test_branded_pages.py
git commit -m "feat(nginx): the branded pages carry the logo, from one shared palette

Inline base64, not a linked file: these pages render when something is
already broken, which is when a second request is least likely to work. The
palette moves into a partial so three pages cannot drift apart.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Rendering the eight documents and the fragment

**Files:**
- Create: `backend/app/templates/nginx/error.html.j2`, `backend/app/templates/nginx/errors.conf.inc.j2`
- Modify: `backend/app/services/nginx/state.py`, `backend/app/services/nginx/renderer.py`
- Test: `backend/tests/test_error_pages_render.py`; append to `backend/tests/test_branded_pages.py`

**Interfaces:**
- Consumes: `ERROR_CODES` (Task 1); `logo_data_uri` (Task 2).
- Produces:
  - `state.ErrorPageSpec(code: int, html: str = "")` — `html` empty means "render the branded template".
  - `state.DesiredState.error_pages: tuple[ErrorPageSpec, ...] = ()`
  - `renderer.ERRORS_CONF = "megoopm-errors.conf.inc"`, `renderer.error_html(code: int) -> str`
  - `renderer.ERROR_COPY: dict[int, tuple[str, str]]` — heading and sentence per code.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_error_pages_render.py`:

```python
"""The eight documents and the fragment that points nginx at them."""

from __future__ import annotations

import pytest
from app.models.error_page import ERROR_CODES
from app.services.nginx.renderer import (
    ERRORS_CONF,
    error_html,
    render_default_site,
)
from app.services.nginx.state import DesiredState, ErrorPageSpec


def test_all_eight_documents_are_written_with_nothing_configured() -> None:
    # Always written, so an `error_page` directive can never point at a file
    # that is not there — which nginx answers with its own bare page.
    files = render_default_site(DesiredState())
    for code in ERROR_CODES:
        assert error_html(code) in files, code


def test_a_document_names_its_own_code_and_copy() -> None:
    files = render_default_site(DesiredState())
    page = files[error_html(502)]
    assert "502" in page
    assert "Bad gateway" in page
    assert "The site behind this address didn&rsquo;t respond correctly." in page
    # And not another code's.
    assert "Gateway timeout" not in page


def test_a_document_never_names_the_instance() -> None:
    # Reachable by anyone: naming a backend tells a prober how this is built.
    page = render_default_site(DesiredState())[error_html(504)]
    for leak in ("upstream", "proxy_pass", "megoopm_upstream", "server_name"):
        assert leak not in page


def test_a_custom_page_replaces_exactly_one_document() -> None:
    state = DesiredState(error_pages=(ErrorPageSpec(code=404, html="<h1>mine</h1>"),))
    files = render_default_site(state)
    assert files[error_html(404)] == "<h1>mine</h1>"
    assert "Bad gateway" in files[error_html(502)]


def test_an_empty_document_falls_back_to_the_branded_page() -> None:
    # The row was edited outside the API and its page is gone. An empty error
    # page is worse than a generic one.
    state = DesiredState(error_pages=(ErrorPageSpec(code=404, html=""),))
    assert "There&rsquo;s nothing at this address." in render_default_site(state)[error_html(404)]


def test_the_fragment_wires_every_code() -> None:
    fragment = render_default_site(DesiredState())[ERRORS_CONF]
    for code in ERROR_CODES:
        assert f"error_page {code} /{error_html(code)};" in fragment
        assert f"location = /{error_html(code)} {{" in fragment
    # Unreachable by a direct request: served only as the result of an error.
    assert fragment.count("internal;") == len(ERROR_CODES)


def test_the_fragment_is_not_parsed_as_a_top_level_conf() -> None:
    # The base config includes `.../*.conf`; this file must not match, or
    # nginx parses `error_page` at the http level and refuses to start.
    assert not ERRORS_CONF.endswith(".conf")
    assert ERRORS_CONF.endswith(".inc")


@pytest.mark.parametrize("code", ERROR_CODES)
def test_every_document_is_self_contained(code: int) -> None:
    page = render_default_site(DesiredState())[error_html(code)]
    assert "data:image/png;base64," in page
    assert "http://" not in page and "https://" not in page
```

Append to `backend/tests/test_branded_pages.py`:

```python

def test_the_error_page_template_obeys_the_same_rules() -> None:
    # The parametrised checks above read finished pages; this one proves the
    # error template joins them rather than inventing its own palette.
    source = (_env().loader.get_source(_env(), "error.html.j2"))[0]
    assert '{% include "_palette.css.j2" %}' in source
    assert "logo_data_uri" in source
```

- [ ] **Step 2: Run them to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_error_pages_render.py tests/test_branded_pages.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — `ImportError: cannot import name 'ERRORS_CONF'`.

- [ ] **Step 3: The spec**

In `backend/app/services/nginx/state.py`, beside `BanPageSpec`:

```python
@dataclass(frozen=True, slots=True)
class ErrorPageSpec:
    """What one HTTP status code is answered with.

    ``html`` is already resolved by the loader — the same division
    :class:`BanPageSpec` makes. Empty means "render the shipped template",
    which also covers a custom page whose document has gone missing: an empty
    error page is worse than a generic one.
    """

    code: int
    html: str = ""
```

and on `DesiredState`, beside `ban_page`:

```python
    #: Only the codes an operator configured; the rest render shipped pages.
    error_pages: tuple[ErrorPageSpec, ...] = ()
```

Add `"ErrorPageSpec"` to that module's `__all__`.

- [ ] **Step 4: The templates**

Create `backend/app/templates/nginx/error.html.j2`:

```jinja
{# One HTTP error, branded.

   Rendered once per code into the shared default directory and served by an
   `internal` location, so it is reachable only as the result of an error
   inside nginx — never by a direct request.

   Nothing here may describe the request, the host, or what is behind the
   proxy: this page is reachable by anyone, and each of those tells someone
   probing the instance how it is built. Same rule banned.html.j2 follows. #}
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{{ code }} &middot; {{ heading }}</title>
    <style>
{% include "_palette.css.j2" %}

      * { box-sizing: border-box; }

      body {
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        padding: 2rem 1.25rem;
        background-color: var(--bg);
        background-image:
          linear-gradient(var(--grid) 1px, transparent 1px),
          linear-gradient(90deg, var(--grid) 1px, transparent 1px);
        background-size: 48px 48px;
        color: var(--fg);
        font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
        line-height: 1.6;
      }

      main {
        width: 100%;
        max-width: 30rem;
        padding: 2.5rem 2rem;
        text-align: center;
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 14px;
        box-shadow: var(--glow);
      }

      .logo {
        display: block;
        width: 64px;
        height: 64px;
        margin: 0 auto 1.25rem;
        filter: var(--logo-glow, none);
      }

      .code {
        margin: 0;
        font-family: ui-monospace, SFMono-Regular, "JetBrains Mono", Menlo, monospace;
        font-size: 3.5rem;
        font-weight: 700;
        line-height: 1;
        letter-spacing: 0.04em;
        color: var(--primary);
      }

      h1 {
        margin: 0.75rem 0 0;
        font-size: 1.25rem;
        font-weight: 600;
      }

      .rule {
        width: 3rem;
        height: 2px;
        margin: 1.25rem auto;
        background: linear-gradient(90deg, var(--primary), var(--accent));
        border-radius: 2px;
      }

      p.detail {
        margin: 0;
        color: var(--muted);
        font-size: 0.95rem;
      }
    </style>
  </head>
  <body>
    <main>
      <img class="logo" src="{{ logo_data_uri }}" alt="MegooPM" width="64" height="64" />
      <p class="code">{{ code }}</p>
      <h1>{{ heading }}</h1>
      <div class="rule"></div>
      <p class="detail">{{ detail }}</p>
    </main>
  </body>
</html>
```

Create `backend/app/templates/nginx/errors.conf.inc.j2`:

```jinja
{# Included once inside every managed `server {}` block.

   `.inc`, not `.conf`: the base config includes `.../*.conf` at the http
   level, where an `error_page` would be a configuration error.

   No `proxy_intercept_errors` anywhere — a status the *backend* returns
   passes through untouched, so an application's own error pages and an API's
   JSON error bodies survive. These pages appear only when nginx itself
   generates the status. #}
# Managed by MegooPM — do not edit by hand.
{%- for code in codes %}
error_page {{ code }} /{{ document(code) }};
location = /{{ document(code) }} {
    root {{ default_dir }};
    internal;
}
{%- endfor %}
```

- [ ] **Step 5: The renderer**

In `backend/app/services/nginx/renderer.py`, beside `BAN_PAGE_HTML`:

```python
# The error documents and the fragment that points nginx at them, written into
# the same shared directory. `.inc` so the base config's `include .../*.conf`
# never parses the fragment at the http level.
ERRORS_CONF = "megoopm-errors.conf.inc"

#: Heading and sentence per code. Each says something true without describing
#: the instance; see error.html.j2.
ERROR_COPY: dict[int, tuple[str, str]] = {
    400: ("Bad request", "The server couldn&rsquo;t understand that request."),
    401: ("Authentication required", "This area needs credentials."),
    403: ("Access denied", "You don&rsquo;t have permission to view this."),
    404: ("Not found", "There&rsquo;s nothing at this address."),
    500: ("Something went wrong", "The site hit an unexpected error."),
    502: ("Bad gateway", "The site behind this address didn&rsquo;t respond correctly."),
    503: ("Service unavailable", "The site is temporarily unable to handle the request."),
    504: ("Gateway timeout", "The site behind this address took too long to answer."),
}


def error_html(code: int) -> str:
    """File name of one status code's document."""
    return f"megoopm-error-{code}.html"
```

and inside `render_default_site`, before the `ban` block:

```python
    # Every code, always: an `error_page` pointing at a missing file gets
    # nginx's own bare page, which is the thing this feature exists to avoid.
    configured = {spec.code: spec.html for spec in state.error_pages}
    for code in ERROR_CODES:
        html = configured.get(code) or ""
        if not html:
            heading, detail = ERROR_COPY[code]
            html = (
                _env()
                .get_template("error.html.j2")
                .render(code=code, heading=heading, detail=detail)
            )
        files[error_html(code)] = html
    files[ERRORS_CONF] = (
        _env()
        .get_template("errors.conf.inc.j2")
        .render(
            codes=ERROR_CODES,
            document=error_html,
            default_dir=settings.nginx_default_dir,
        )
    )
```

Import `ERROR_CODES` from `app.models.error_page` at the top, and add
`"ERRORS_CONF"`, `"ERROR_COPY"`, `"error_html"` to `__all__`.

- [ ] **Step 6: Run, lint, commit**

```bash
docker exec megoopm-test ruff format app/services/nginx/state.py app/services/nginx/renderer.py tests/test_error_pages_render.py tests/test_branded_pages.py
docker exec megoopm-test python -m pytest tests/test_error_pages_render.py tests/test_branded_pages.py tests/test_default_site_render.py tests/test_nginx_engine.py -p no:cacheprovider -p no:warnings
docker exec megoopm-test ruff check app tests
```
Commit separately:
```bash
git add backend/app/services/nginx backend/app/templates/nginx backend/tests/test_error_pages_render.py backend/tests/test_branded_pages.py
git commit -m "feat(nginx): render the eight error documents and the fragment that wires them

All eight are always written: an error_page pointing at a missing file gets
nginx's own bare page, which is what this exists to avoid.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Every server block includes the fragment

**Files:**
- Modify: `backend/app/templates/nginx/server.conf.j2`, `redirect.conf.j2`, `dead.conf.j2`, `default_tls.conf.j2`, `default_site.conf.j2`
- Modify: `backend/app/services/nginx/renderer.py` (pass `default_dir` where a template lacks it)
- Test: `backend/tests/test_error_pages_blocks.py`

**Interfaces:**
- Consumes: `ERRORS_CONF` (Task 3).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_error_pages_blocks.py`:

```python
"""Every managed `server {}` block includes the error fragment.

Counted per block, not per file: a TLS host renders two servers, and one of
them silently missing the include is exactly the bug this catches.
"""

from __future__ import annotations

from app.services.nginx import render_config
from app.services.nginx.renderer import ERRORS_CONF
from app.services.nginx.state import (
    BackendSpec,
    CertificateSpec,
    DeadHostSpec,
    DesiredState,
    ProxyHostSpec,
    RedirectionHostSpec,
    UpstreamSpec,
)

CERT = CertificateSpec(
    id=3,
    fullchain_path="/etc/nginx/certs/3/fullchain.pem",
    privkey_path="/etc/nginx/certs/3/privkey.pem",
)


def _pool() -> UpstreamSpec:
    return UpstreamSpec(
        id=1,
        name="web-pool",
        lb_method="round_robin",
        backends=(BackendSpec(host="10.0.0.1", port=8080),),
    )


def _assert_every_block_includes(config: str) -> None:
    blocks = config.count("\nserver {")
    assert blocks > 0
    assert config.count(f"include ") >= blocks
    assert config.count(ERRORS_CONF) == blocks, config


def test_a_plain_proxy_host_includes_it() -> None:
    state = DesiredState(
        proxy_hosts=(ProxyHostSpec(id=1, domain_names=("a.example.com",), upstream_id=1),),
        http_upstreams=(_pool(),),
    )
    _assert_every_block_includes(render_config(state)["megoopm-proxy-1.conf"])


def test_a_tls_proxy_host_includes_it_in_both_servers() -> None:
    host = ProxyHostSpec(
        id=1, domain_names=("a.example.com",), upstream_id=1, certificate=CERT, ssl_forced=False
    )
    state = DesiredState(proxy_hosts=(host,), http_upstreams=(_pool(),))
    config = render_config(state)["megoopm-proxy-1.conf"]
    assert config.count(ERRORS_CONF) == 2


def test_a_redirection_host_includes_it() -> None:
    state = DesiredState(
        redirection_hosts=(
            RedirectionHostSpec(
                id=1,
                domain_names=("r.example.com",),
                forward_domain_name="example.com",
                forward_scheme="auto",
                forward_http_code=301,
            ),
        )
    )
    _assert_every_block_includes(render_config(state)["megoopm-redirect-1.conf"])


def test_a_dead_host_includes_it() -> None:
    state = DesiredState(dead_hosts=(DeadHostSpec(id=1, domain_names=("d.example.com",)),))
    _assert_every_block_includes(render_config(state)["megoopm-dead-1.conf"])
```

> Before writing this, open `backend/tests/test_nginx_render.py` and copy the
> exact constructor arguments its `RedirectionHostSpec` and `DeadHostSpec`
> fixtures use — the field names above are from the spec's description, and a
> mismatch fails at construction rather than on the assertion.

- [ ] **Step 2: Run it to verify it fails**

```bash
docker exec megoopm-test python -m pytest tests/test_error_pages_blocks.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — `assert 0 == 1`, the fragment is in no block yet.

- [ ] **Step 3: Add the include**

Each of the four templates renders its `server {}` blocks through macros or
inline. In **every** `server {` block of `server.conf.j2`, `redirect.conf.j2`,
`dead.conf.j2` and `default_tls.conf.j2`, add as the first line of the block
body:

```jinja
    include {{ default_dir }}/{{ errors_conf }};
```

Prefer a macro where the template already has one (`server.conf.j2` and
`dead.conf.j2` use macros for shared fragments) so the line appears once per
template rather than three times.

Every one of those templates is rendered by a `_render_*` function in
`renderer.py`; each needs two more arguments:

```python
        default_dir=settings.nginx_default_dir,
        errors_conf=ERRORS_CONF,
```

`_render_proxy_host` already passes `default_dir` (added by the location-target
work); the other three do not. `StrictUndefined` means a template referencing
an argument its renderer does not pass fails loudly at apply time, so run the
whole render suite before moving on.

- [ ] **Step 4: Run, lint, commit**

```bash
docker exec megoopm-test ruff format app/services/nginx/renderer.py tests/test_error_pages_blocks.py
docker exec megoopm-test python -m pytest tests/test_error_pages_blocks.py tests/test_nginx_render.py tests/test_meg24_render.py tests/test_access_list_render.py tests/test_certs_render.py tests/test_nginx_default_tls.py -p no:cacheprovider -p no:warnings
docker exec megoopm-test ruff check app tests
```
Commit separately:
```bash
git add backend/app/templates/nginx backend/app/services/nginx/renderer.py backend/tests/test_error_pages_blocks.py
git commit -m "feat(nginx): every managed server block serves the branded error pages

Counted per block rather than per file: a TLS host renders two servers, and
one of them missing the include is the bug worth catching.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Loading the rows, and the API

**Files:**
- Create: `backend/app/services/error_page.py`, `backend/app/schemas/error_page.py`
- Modify: `backend/app/services/nginx/loader.py`, `backend/app/api/routes/settings.py`, `backend/openapi.json`
- Test: `backend/tests/test_error_pages_api.py`

**Interfaces:**
- Consumes: `ErrorPage`, `ERROR_CODES` (Task 1); `ErrorPageSpec` (Task 3).
- Produces:
  - `schemas.error_page.ErrorPageRead(code: int, mode: ErrorPageMode, custom_page_id: int | None)`
  - `schemas.error_page.ErrorPageUpdate(code: int, mode: ErrorPageMode, custom_page_id: int | None = None)` — validates that the page is present exactly when the mode is `custom_page`, and that the code is one of the eight
  - `services.error_page.list_error_pages(db) -> list[ErrorPageRead]` — always eight, in `ERROR_CODES` order
  - `services.error_page.replace_error_pages(db, rows: list[ErrorPageUpdate]) -> list[ErrorPageRead]`; raises `UnknownCustomPageError`
  - `GET /settings/error-pages`, `PUT /settings/error-pages`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_error_pages_api.py`:

```python
"""The Error pages settings card's two routes."""

from __future__ import annotations

import pytest
from app.models.error_page import ERROR_CODES
from httpx import AsyncClient

URL = "/api/v1/settings/error-pages"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _make_page(db_client: AsyncClient, token: str, name: str = "Oops") -> int:
    resp = await db_client.post(
        "/api/v1/custom-pages",
        headers=_auth(token),
        json={"name": name, "description": "", "html": "<h1>mine</h1>"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_all_eight_codes_come_back_defaulted(db_client: AsyncClient, admin_token: str) -> None:
    # The card renders rows from this, so an unconfigured instance must still
    # answer with the full set rather than an empty list.
    body = (await db_client.get(URL, headers=_auth(admin_token))).json()
    assert [row["code"] for row in body] == list(ERROR_CODES)
    assert {row["mode"] for row in body} == {"default"}
    assert all(row["custom_page_id"] is None for row in body)


async def test_a_whole_set_write_stores_only_what_differs(
    db_client: AsyncClient, admin_token: str
) -> None:
    page_id = await _make_page(db_client, admin_token)
    payload = [
        {"code": code, "mode": "default", "custom_page_id": None} for code in ERROR_CODES
    ]
    payload[3] = {"code": 404, "mode": "custom_page", "custom_page_id": page_id}

    resp = await db_client.put(URL, headers=_auth(admin_token), json=payload)

    assert resp.status_code == 200, resp.text
    stored = {row["code"]: row for row in resp.json()}
    assert stored[404]["mode"] == "custom_page"
    assert stored[404]["custom_page_id"] == page_id
    assert stored[502]["mode"] == "default"


async def test_setting_a_code_back_to_default_deletes_its_row(
    db_client: AsyncClient, admin_token: str, session_factory
) -> None:
    from app.models.error_page import ErrorPage
    from sqlalchemy import select

    page_id = await _make_page(db_client, admin_token)
    payload = [{"code": c, "mode": "default", "custom_page_id": None} for c in ERROR_CODES]
    payload[3] = {"code": 404, "mode": "custom_page", "custom_page_id": page_id}
    await db_client.put(URL, headers=_auth(admin_token), json=payload)

    payload[3] = {"code": 404, "mode": "default", "custom_page_id": None}
    await db_client.put(URL, headers=_auth(admin_token), json=payload)

    async with session_factory() as db:
        rows = (await db.execute(select(ErrorPage))).scalars().all()
    # Nothing configured means nothing stored.
    assert rows == []


@pytest.mark.parametrize(
    ("row", "fragment"),
    [
        ({"code": 404, "mode": "custom_page", "custom_page_id": None}, "page"),
        ({"code": 404, "mode": "default", "custom_page_id": 1}, "page"),
        ({"code": 418, "mode": "default", "custom_page_id": None}, "418"),
    ],
)
async def test_a_bad_row_is_422(db_client: AsyncClient, admin_token: str, row, fragment) -> None:
    resp = await db_client.put(URL, headers=_auth(admin_token), json=[row])
    assert resp.status_code == 422, resp.text
    assert fragment in resp.text


async def test_a_missing_page_is_422_naming_the_code(
    db_client: AsyncClient, admin_token: str
) -> None:
    resp = await db_client.put(
        URL,
        headers=_auth(admin_token),
        json=[{"code": 404, "mode": "custom_page", "custom_page_id": 9999}],
    )
    assert resp.status_code == 422
    assert "404" in resp.json()["detail"]


async def test_both_routes_are_admin_only(db_client: AsyncClient, member_token: str) -> None:
    assert (await db_client.get(URL, headers=_auth(member_token))).status_code == 403
    assert (await db_client.put(URL, headers=_auth(member_token), json=[])).status_code == 403


async def test_a_write_is_audited(
    db_client: AsyncClient, admin_token: str, session_factory
) -> None:
    from app.models.audit_log import AuditLog
    from sqlalchemy import select

    page_id = await _make_page(db_client, admin_token)
    await db_client.put(
        URL,
        headers=_auth(admin_token),
        json=[{"code": 404, "mode": "custom_page", "custom_page_id": page_id}],
    )
    async with session_factory() as db:
        rows = (await db.execute(select(AuditLog))).scalars().all()
    assert any(r.object_type == "error_page" for r in rows)
```

> Check `tests/test_custom_pages_api.py` for the real create-page payload and
> status before relying on `_make_page`; if custom pages are not creatable in
> the SQLite-backed `db_client` fixture, insert the row through
> `session_factory` instead.

- [ ] **Step 2: Run them to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_error_pages_api.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — 404 on both routes.

- [ ] **Step 3: The schemas**

Create `backend/app/schemas/error_page.py`:

```python
"""What each common HTTP status is answered with.

The API always speaks in the full set of eight: the settings card renders one
row per code and saves them together, so a partial write would leave the
operator guessing which rows took effect.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import ErrorPageMode
from app.models.error_page import ERROR_CODES


class ErrorPageBase(BaseModel):
    code: int = Field(description="One of the eight codes MegooPM brands")
    mode: ErrorPageMode
    custom_page_id: int | None = Field(
        default=None, description="Page served when the mode is 'custom_page'"
    )

    @model_validator(mode="after")
    def _coherent(self) -> ErrorPageBase:
        """Mirror the DB constraint so the API answers 422, not a 500."""
        if self.code not in ERROR_CODES:
            raise ValueError(f"{self.code} is not one of the codes MegooPM brands.")
        if self.mode is ErrorPageMode.custom_page and self.custom_page_id is None:
            raise ValueError(f"Choose a page for {self.code}, or use the MegooPM page.")
        if self.mode is ErrorPageMode.default and self.custom_page_id is not None:
            raise ValueError(f"The MegooPM page for {self.code} takes no page of its own.")
        return self


class ErrorPageRead(ErrorPageBase):
    """One code's effective setting. A code with no row reads as 'default'."""

    model_config = ConfigDict(from_attributes=True)


class ErrorPageUpdate(ErrorPageBase):
    """One row of a whole-set write."""


__all__ = ["ErrorPageRead", "ErrorPageUpdate"]
```

- [ ] **Step 4: The service**

Create `backend/app/services/error_page.py`:

```python
"""Read and replace the error-page bindings.

Only configured codes are stored; the read fills the gaps. That keeps a fresh
install free of rows nobody chose and makes "back to the MegooPM page" a
delete rather than a second way to say default.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.custom_page import CustomPage
from app.models.enums import ErrorPageMode
from app.models.error_page import ERROR_CODES, ErrorPage
from app.schemas.error_page import ErrorPageRead, ErrorPageUpdate


class UnknownCustomPageError(Exception):
    """A referenced page does not exist. The route answers 422."""


async def list_error_pages(db: AsyncSession) -> list[ErrorPageRead]:
    """All eight codes, in order, with the effective setting for each."""
    rows = {row.code: row for row in (await db.scalars(select(ErrorPage))).all()}
    out: list[ErrorPageRead] = []
    for code in ERROR_CODES:
        row = rows.get(code)
        if row is None:
            out.append(
                ErrorPageRead(code=code, mode=ErrorPageMode.default, custom_page_id=None)
            )
        else:
            out.append(ErrorPageRead.model_validate(row))
    return out


async def replace_error_pages(
    db: AsyncSession, rows: list[ErrorPageUpdate]
) -> list[ErrorPageRead]:
    """Replace the whole set. Codes set to 'default' lose their row."""
    wanted = {row.code: row for row in rows if row.mode is ErrorPageMode.custom_page}

    ids = {row.custom_page_id for row in wanted.values() if row.custom_page_id is not None}
    if ids:
        found = set((await db.scalars(select(CustomPage.id).where(CustomPage.id.in_(ids)))).all())
        missing = {
            code: row.custom_page_id
            for code, row in wanted.items()
            if row.custom_page_id not in found
        }
        if missing:
            # Named by code: the card shows eight rows and "a page is missing"
            # would not say which one to fix.
            detail = ", ".join(f"{code} (page {page})" for code, page in sorted(missing.items()))
            raise UnknownCustomPageError(f"No such page for: {detail}")

    await db.execute(delete(ErrorPage))
    for code, row in sorted(wanted.items()):
        db.add(ErrorPage(code=code, mode=row.mode, custom_page_id=row.custom_page_id))
    await db.commit()
    return await list_error_pages(db)


__all__ = ["UnknownCustomPageError", "list_error_pages", "replace_error_pages"]
```

- [ ] **Step 5: The loader and the routes**

In `backend/app/services/nginx/loader.py`, beside `_load_ban_page`:

```python
async def _load_error_pages(session: AsyncSession) -> tuple[ErrorPageSpec, ...]:
    """Configured codes only, with each document dereferenced.

    A row whose page has gone missing (edited outside the API — the FK is
    RESTRICT) yields an empty ``html``, which the renderer reads as "use the
    shipped page". An empty error page would be worse than a generic one.
    """
    stmt = select(ErrorPage).options(selectinload(ErrorPage.custom_page))
    rows = (await session.scalars(stmt)).all()
    specs: list[ErrorPageSpec] = []
    for row in rows:
        page = row.custom_page
        specs.append(ErrorPageSpec(code=row.code, html=page.html if page is not None else ""))
    return tuple(sorted(specs, key=lambda s: s.code))
```

Call it beside `ban_page = await _load_ban_page(session)` and pass
`error_pages=await _load_error_pages(session)` into `DesiredState(...)`.
Import `ErrorPage` and `ErrorPageSpec`.

In `backend/app/api/routes/settings.py`:

```python
@router.get("/error-pages", response_model=list[ErrorPageRead])
async def read_error_pages(_admin: AdminUser, db: SessionDep) -> list[ErrorPageRead]:
    """What each branded status code is answered with. Always all eight."""
    return await error_page_service.list_error_pages(db)


@router.put("/error-pages", response_model=list[ErrorPageRead])
async def update_error_pages(
    body: list[ErrorPageUpdate],
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> list[ErrorPageRead]:
    """Replace the whole set. Admin-only.

    ``after_config_write``, not a bare audit: these choices are rendered into
    every server block, so nginx must be rewritten and reloaded for a change
    to take effect at all.
    """
    try:
        rows = await error_page_service.replace_error_pages(db, body)
    except error_page_service.UnknownCustomPageError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.update,
        object_type="error_page",
        object_id=None,
        meta={"configured": [r.code for r in rows if r.mode is ErrorPageMode.custom_page]},
    )
    return rows
```

Import `ErrorPageRead`, `ErrorPageUpdate`, `ErrorPageMode`, and
`from app.services import error_page as error_page_service`.

- [ ] **Step 6: Run, regenerate, full suite, commit**

```bash
docker exec megoopm-test ruff format app/schemas/error_page.py app/services/error_page.py app/services/nginx/loader.py app/api/routes/settings.py tests/test_error_pages_api.py
docker exec megoopm-test python -m pytest tests/test_error_pages_api.py tests/test_settings_api.py tests/test_nginx_api.py -p no:cacheprovider -p no:warnings
docker exec megoopm-test python -m scripts.export_openapi
docker exec megoopm-test ruff check app tests
docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings
```
Commit separately:
```bash
git add backend/app/schemas/error_page.py backend/app/services backend/app/api/routes/settings.py backend/tests/test_error_pages_api.py backend/openapi.json
git commit -m "feat(error-pages): read the effective set, replace it as a whole

A whole-set write because the card saves once; a partial one would leave the
operator guessing which of eight rows took effect. Codes set back to the
MegooPM page lose their row entirely.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: The settings card

**Files:**
- Modify: `frontend/src/lib/api/generated/schema.ts` (regenerated), `frontend/src/lib/api/resources/settings.ts`, `frontend/src/lib/api/index.ts`, `frontend/src/components/settings/settings-view.tsx`
- Create: `frontend/src/components/settings/error-pages-card.tsx`
- Test: `frontend/src/components/settings/error-pages-card.test.tsx`

**Interfaces:**
- Consumes: `GET`/`PUT /settings/error-pages` (Task 5).
- Produces:
  - `instanceSettings.listErrorPages()`, `instanceSettings.updateErrorPages(body)`
  - types `ErrorPageRead`, `ErrorPageUpdate`, `ErrorPageMode`
  - `ErrorPagesCard({ pages }: { pages: CustomPageSummary[] })` — self-loading

- [ ] **Step 1: Regenerate and add the calls**

```bash
cd frontend && npm run gen:api
```

In `src/lib/api/resources/settings.ts`:

```ts
export type ErrorPageRead = Schemas["ErrorPageRead"];
export type ErrorPageUpdate = Schemas["ErrorPageUpdate"];
export type ErrorPageMode = Schemas["ErrorPageMode"];
```
```ts
  /** All eight branded codes with their effective setting. */
  listErrorPages: () => api.get<ErrorPageRead[]>(`${BASE}/error-pages`),
  /** Replace the whole set; nginx is rewritten and reloaded. */
  updateErrorPages: (body: ErrorPageUpdate[]) =>
    api.put<ErrorPageRead[]>(`${BASE}/error-pages`, body),
```

Re-export the three types from `src/lib/api/index.ts`.

- [ ] **Step 2: Write the failing tests**

Create `frontend/src/components/settings/error-pages-card.test.tsx`:

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { instanceSettings } from "@/lib/api";
import { ErrorPagesCard } from "@/components/settings/error-pages-card";

const CODES = [400, 401, 403, 404, 500, 502, 503, 504];
const DEFAULTS = CODES.map((code) => ({
  code,
  mode: "default" as const,
  custom_page_id: null,
}));
const PAGES = [{ id: 4, name: "Maintenance" }] as never;

beforeEach(() => {
  vi.spyOn(instanceSettings, "listErrorPages").mockResolvedValue(DEFAULTS);
  vi.spyOn(toast, "success").mockImplementation(() => "" as never);
  vi.spyOn(toast, "error").mockImplementation(() => "" as never);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ErrorPagesCard", () => {
  it("lists every branded code with its name", async () => {
    render(<ErrorPagesCard pages={PAGES} />);
    for (const code of CODES) {
      expect(await screen.findByText(String(code))).toBeInTheDocument();
    }
    expect(screen.getByText("Bad gateway")).toBeInTheDocument();
  });

  it("shows a page picker only for the custom mode", async () => {
    const user = userEvent.setup();
    render(<ErrorPagesCard pages={PAGES} />);
    const row = (await screen.findByText("404")).closest("tr")!;
    expect(within(row).queryByRole("combobox", { name: /page for 404/i })).not.toBeInTheDocument();

    await user.click(within(row).getByRole("combobox", { name: /answer for 404/i }));
    await user.click(await screen.findByRole("option", { name: "Custom page" }));

    expect(within(row).getByRole("combobox", { name: /page for 404/i })).toBeInTheDocument();
  });

  it("keeps Save disabled until something changes", async () => {
    const user = userEvent.setup();
    render(<ErrorPagesCard pages={PAGES} />);
    const save = await screen.findByRole("button", { name: /save error pages/i });
    expect(save).toBeDisabled();

    const row = screen.getByText("404").closest("tr")!;
    await user.click(within(row).getByRole("combobox", { name: /answer for 404/i }));
    await user.click(await screen.findByRole("option", { name: "Custom page" }));

    expect(save).toBeEnabled();
  });

  it("sends all eight rows, with the page on the one that changed", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(instanceSettings, "updateErrorPages").mockResolvedValue(DEFAULTS);
    render(<ErrorPagesCard pages={PAGES} />);
    const row = (await screen.findByText("404")).closest("tr")!;

    await user.click(within(row).getByRole("combobox", { name: /answer for 404/i }));
    await user.click(await screen.findByRole("option", { name: "Custom page" }));
    await user.click(within(row).getByRole("combobox", { name: /page for 404/i }));
    await user.click(await screen.findByRole("option", { name: "Maintenance" }));
    await user.click(screen.getByRole("button", { name: /save error pages/i }));

    await waitFor(() => expect(update).toHaveBeenCalled());
    const sent = update.mock.calls[0][0];
    expect(sent).toHaveLength(8);
    expect(sent.find((r) => r.code === 404)).toEqual({
      code: 404,
      mode: "custom_page",
      custom_page_id: 4,
    });
    expect(sent.find((r) => r.code === 502)).toEqual({
      code: 502,
      mode: "default",
      custom_page_id: null,
    });
  });

  it("shows a load failure instead of an empty card", async () => {
    vi.mocked(instanceSettings.listErrorPages).mockRejectedValue(new Error("boom"));
    render(<ErrorPagesCard pages={PAGES} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });
});
```

- [ ] **Step 3: Run them to verify they fail**

```bash
cd frontend && npx vitest run src/components/settings/error-pages-card.test.tsx
```
Expected: FAIL — module not found.

- [ ] **Step 4: The card**

Create `frontend/src/components/settings/error-pages-card.tsx`:

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import {
  instanceSettings,
  type CustomPageSummary,
  type ErrorPageMode,
  type ErrorPageRead,
} from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

/** Matches ERROR_COPY in the renderer; the card names what it is binding. */
const CODE_NAMES: Record<number, string> = {
  400: "Bad request",
  401: "Authentication required",
  403: "Access denied",
  404: "Not found",
  500: "Something went wrong",
  502: "Bad gateway",
  503: "Service unavailable",
  504: "Gateway timeout",
};

const MODE_LABELS: Record<ErrorPageMode, string> = {
  default: "MegooPM page",
  custom_page: "Custom page",
};

type Row = { code: number; mode: ErrorPageMode; custom_page_id: number | null };

function sameSet(a: Row[], b: Row[]): boolean {
  return (
    a.length === b.length &&
    a.every((row, i) => row.mode === b[i].mode && row.custom_page_id === b[i].custom_page_id)
  );
}

/**
 * What each common HTTP error is answered with, instance-wide.
 *
 * Saved as a whole set rather than row by row: the API replaces all eight, so
 * a per-row save would leave the operator guessing which took effect.
 */
export function ErrorPagesCard({ pages }: { pages: CustomPageSummary[] }) {
  const [stored, setStored] = useState<Row[] | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const list: ErrorPageRead[] = await instanceSettings.listErrorPages();
      const next = list.map((r) => ({
        code: r.code,
        mode: r.mode,
        custom_page_id: r.custom_page_id ?? null,
      }));
      setStored(next);
      setRows(next);
      setError(null);
    } catch (err) {
      setError(describeError(err).message);
    }
  }, []);

  useEffect(() => {
    let active = true;
    void (async () => {
      if (active) await load();
    })();
    return () => {
      active = false;
    };
  }, [load]);

  function patch(code: number, change: Partial<Row>) {
    setRows((current) =>
      current.map((row) => (row.code === code ? { ...row, ...change } : row)),
    );
  }

  async function save() {
    setSaving(true);
    try {
      const next = await instanceSettings.updateErrorPages(
        rows.map((row) => ({
          code: row.code,
          mode: row.mode,
          // Never send a page the mode does not use: the API rejects it, and
          // the payload would describe two configurations at once.
          custom_page_id: row.mode === "custom_page" ? row.custom_page_id : null,
        })),
      );
      const applied = next.map((r) => ({
        code: r.code,
        mode: r.mode,
        custom_page_id: r.custom_page_id ?? null,
      }));
      setStored(applied);
      setRows(applied);
      toast.success("Error pages saved");
    } catch (err) {
      toast.error(describeError(err).message);
    } finally {
      setSaving(false);
    }
  }

  const dirty = stored !== null && !sameSet(rows, stored);
  const incomplete = rows.some((r) => r.mode === "custom_page" && r.custom_page_id === null);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <TriangleAlert className="size-4" /> Error pages
        </CardTitle>
        <CardDescription>
          What a visitor sees when MegooPM itself answers with an error, on every domain this
          instance serves. Errors your own application returns are passed through untouched.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {error ? (
          <p role="alert" className="text-destructive text-sm">
            {error}
          </p>
        ) : stored === null ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-16">Code</TableHead>
                <TableHead>Meaning</TableHead>
                <TableHead className="w-44">Answer with</TableHead>
                <TableHead className="w-56">Page</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.code}>
                  <TableCell className="font-mono text-xs font-medium">{row.code}</TableCell>
                  <TableCell className="text-muted-foreground text-sm">
                    {CODE_NAMES[row.code]}
                  </TableCell>
                  <TableCell>
                    <Select
                      value={row.mode}
                      onValueChange={(v) =>
                        patch(row.code, {
                          mode: v as ErrorPageMode,
                          custom_page_id: v === "custom_page" ? row.custom_page_id : null,
                        })
                      }
                      items={MODE_LABELS}
                    >
                      <SelectTrigger aria-label={`Answer for ${row.code}`} disabled={saving}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {(["default", "custom_page"] as const).map((mode) => (
                          <SelectItem key={mode} value={mode}>
                            {MODE_LABELS[mode]}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </TableCell>
                  <TableCell>
                    {row.mode === "custom_page" ? (
                      <Select
                        value={row.custom_page_id === null ? "" : String(row.custom_page_id)}
                        onValueChange={(v) =>
                          patch(row.code, { custom_page_id: Number(v as string) })
                        }
                        items={Object.fromEntries(pages.map((p) => [String(p.id), p.name]))}
                      >
                        <SelectTrigger
                          aria-label={`Page for ${row.code}`}
                          disabled={saving || pages.length === 0}
                        >
                          <SelectValue
                            placeholder={pages.length === 0 ? "No pages yet" : "Choose a page"}
                          />
                        </SelectTrigger>
                        <SelectContent>
                          {pages.map((page) => (
                            <SelectItem key={page.id} value={String(page.id)}>
                              {page.name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : null}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
      <CardFooter className="justify-end">
        <Button onClick={() => void save()} disabled={!dirty || incomplete || saving}>
          {saving ? "Saving…" : "Save error pages"}
        </Button>
      </CardFooter>
    </Card>
  );
}
```

- [ ] **Step 5: Mount it**

In `frontend/src/components/settings/settings-view.tsx`, import the card and
render it after the ban-page card, passing the `pages` that view already
loads:

```tsx
      <ErrorPagesCard pages={pages} />
```

Check whether `settings-view.test.tsx` mocks the settings API: if it does,
add `listErrorPages` to those mocks, or mock the whole card as the profile
view mocks its passkeys card.

- [ ] **Step 6: Run, typecheck, lint, full suite; commit**

```bash
cd frontend && npx prettier --write --print-width 100 src/components/settings src/lib/api
npx vitest run src/components/settings && npx tsc --noEmit && npm run lint && npx vitest run
```
Commit separately:
```bash
git add frontend/src
git commit -m "feat(settings): an Error pages card, one row per branded code

Saved as a whole set, matching the API: a per-row save would leave the
operator guessing which of eight took effect.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Docs, and tearing down

**Files:**
- Modify: `docs/nginx.md` (or the doc that describes the rendered files — check `docs/` for the one covering `render_default_site`)

- [ ] **Step 1: Document the shared directory's new contents**

Find the doc describing the default directory (`grep -rln "megoopm-default.html" docs/`) and add a section covering: the eight documents and the fragment; that only nginx-generated errors are branded and why; that the fragment is `.inc` so the base config does not parse it; that a code with no row means the shipped page; and how to verify on a live stack:

```bash
docker compose exec nginx ls /data/nginx/default/
curl -sI https://<a-managed-domain>/definitely-not-here | head -1   # 404
docker compose exec nginx cat /data/nginx/default/megoopm-errors.conf.inc
```

- [ ] **Step 2: Full suites, then tear down**

```bash
docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings
cd frontend && npx vitest run && npx tsc --noEmit && npm run lint
```
Commit separately, then:
```bash
export MSYS_NO_PATHCONV=1
docker rm -f megoopm-test megoopm-testdb && docker network rm megoopm-testnet
```

---

## Manual verification

- [ ] Rebuild and start the stack; run the migration. Settings shows the
      Error pages card with eight rows, all reading "MegooPM page".
- [ ] Visit a managed domain at a path that does not exist. The branded 404
      appears, with the logo, and switches between light and dark with the OS
      setting.
- [ ] Stop a backend and reload a proxied host: the branded 502 appears.
- [ ] `docker compose exec nginx cat /data/nginx/default/megoopm-errors.conf.inc`
      lists all eight codes.
- [ ] Request `/megoopm-error-404.html` directly: nginx answers 404, not the
      document — that is `internal` working.
- [ ] Bind 404 to a custom page, save, and reload: the custom document is
      served. Set it back to the MegooPM page and confirm the branded one
      returns.
- [ ] Confirm an application's own error page still shows: point a host at a
      backend that returns its own 404 body and check it is unchanged.
- [ ] Load a branded page with the network throttled to offline after first
      byte: it must render completely, since nothing is fetched.
