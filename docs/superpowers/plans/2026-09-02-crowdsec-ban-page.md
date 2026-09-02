# CrowdSec Ban Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator choose what a CrowdSec-blocked visitor sees — a MegooPM page (the default), any Custom Page, or nothing at all — instead of today's bare `403`.

**Architecture:** `BAN_TEMPLATE_PATH` points at one fixed path and the *presence of that file* decides the behaviour, so "no page" needs no special casing in nginx or Lua. The file is written into the directory that already holds `megoopm-default.html`, so it reuses the existing reconciliation target, volume and data-init. The setting is two columns on the `instance_settings` singleton, mirroring the default-site pair.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 (async), Alembic, Pydantic v2, Jinja2 (`StrictUndefined`), pytest; Next.js 16, React 19, base-ui, vitest; OpenResty + lua-cs-bouncer.

**Spec:** `docs/superpowers/specs/2026-09-02-crowdsec-ban-page-design.md`

## Global Constraints

- **The migration MUST create the enum type explicitly.** `op.add_column` does *not* emit `CREATE TYPE` — only `create_table` does. This is the exact inverse of the note in `0019_instance_settings.py` ("No explicit `_MODE.create()`"), which applies only because that migration creates the table. Getting this backwards fails with `UndefinedObject` on upgrade or `DuplicateObjectError` on a re-run.
- **The ban-page route MUST use `after_config_write`**, like `PATCH /settings/default-site` and unlike `PATCH /settings/llm`. It changes a file nginx serves, so without it the setting is stored and nothing is ever written or reloaded.
- **Mode `none` writes no file.** Not an empty file — `ban.lua` guards with `utils.file_exist`, so an empty file would serve a blank page with a 403 instead of nginx's stock 403.
- **The shipped page is static.** The bouncer reads it once at init and emits it verbatim; it can carry no request data, and must not describe the visitor's IP, the matched decision, or the ban duration.
- Run backend tests in a Linux container — the app imports `fcntl`. Start it once, with Postgres attached so the DB-gated suites do not silently skip:

```bash
export MSYS_NO_PATHCONV=1
docker network create megoopm-testnet
docker run -d --name megoopm-testdb --network megoopm-testnet \
  -e POSTGRES_USER=megoopm -e POSTGRES_PASSWORD=megoopm -e POSTGRES_DB=megoopm postgres:16-alpine
docker run -d --name megoopm-test --user root --network megoopm-testnet \
  -v "C:/Projects/megoopm/backend:/src" -w /src \
  -e CELERY_TASK_ALWAYS_EAGER=true -e CELERY_RESULT_BACKEND=cache+memory:// \
  -e DATABASE_URL="postgresql+asyncpg://megoopm:megoopm@megoopm-testdb:5432/megoopm" \
  --entrypoint sleep megoopm-backend infinity
docker exec megoopm-test pip install -q "pytest>=8.2" "pytest-asyncio>=0.23" "aiosqlite>=0.20" "ruff>=0.6"
```

  Do NOT mount the working tree over `/app`: it shadows the image's entrypoint with the host's CRLF copy and the container dies on `bash\r`. Tear down with `docker rm -f megoopm-test megoopm-testdb && docker network rm megoopm-testnet`.
- Changing the schema breaks `tests/test_openapi.py::test_committed_openapi_is_in_sync`. Refresh with `docker exec megoopm-test python -m scripts.export_openapi`, then `cd frontend && npm run gen:api`.

---

### Task 1: The setting — enum, columns, migration, API

**Files:**
- Modify: `backend/app/models/enums.py`
- Modify: `backend/app/models/instance_settings.py`
- Create: `backend/alembic/versions/0021_crowdsec_ban_page.py`
- Modify: `backend/app/schemas/instance_settings.py`
- Modify: `backend/app/services/instance_settings.py`
- Modify: `backend/app/api/routes/settings.py`
- Test: `backend/tests/test_settings_api.py`

**Interfaces:**
- Produces:
  - `CrowdSecBanMode` (`megoopm` | `custom_page` | `none`) in `app/models/enums.py`
  - `InstanceSettings.crowdsec_ban_mode`, `InstanceSettings.crowdsec_ban_page_id`
  - `CrowdSecBanUpdate` in `app/schemas/instance_settings.py`
  - `settings_service.update_ban_page(db, changes) -> InstanceSettings`
  - `PATCH /api/v1/settings/ban-page`

- [x] **Step 1: Add the enum**

In `backend/app/models/enums.py`, after `DefaultSiteMode`:

```python
class CrowdSecBanMode(enum.StrEnum):
    """What a CrowdSec-blocked visitor is served.

    ``none`` is not "unset": it is the deliberate choice to write no template
    file, so the bouncer answers a bare 403 as it did before this setting
    existed. Some operators prefer that a block does not advertise which
    product is in front.
    """

    megoopm = "megoopm"
    custom_page = "custom_page"
    none = "none"
```

- [x] **Step 2: Add the columns**

In `backend/app/models/instance_settings.py`, after `default_site_page_id`, and add `CrowdSecBanMode` to the `app.models.enums` import:

```python
    # --- CrowdSec ban page ----------------------------------------------
    crowdsec_ban_mode: Mapped[CrowdSecBanMode] = mapped_column(
        Enum(
            CrowdSecBanMode,
            name="crowdsec_ban_mode",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=CrowdSecBanMode.megoopm,
        server_default=CrowdSecBanMode.megoopm.value,
    )
    # RESTRICT for the same reason as default_site_page_id: silently changing
    # what every blocked visitor sees is worse than refusing the delete.
    crowdsec_ban_page_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("custom_pages.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
```

- [x] **Step 3: Write the migration**

Create `backend/alembic/versions/0021_crowdsec_ban_page.py`:

```python
"""CrowdSec ban page selection on the instance-settings singleton

Two columns: which page a blocked visitor is served, and the custom page it
refers to. Defaults to the MegooPM page, so an upgrade replaces the previous
bare 403 without anyone visiting Settings.

Revision ID: 0021_crowdsec_ban_page
Revises: 0020_llm_settings
Create Date: 2026-09-02 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021_crowdsec_ban_page"
down_revision: str | None = "0020_llm_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BAN_MODE = sa.Enum(
    "megoopm",
    "custom_page",
    "none",
    name="crowdsec_ban_mode",
)


def upgrade() -> None:
    # UNLIKE 0019, the type must be created by hand. That migration relies on
    # create_table emitting CREATE TYPE; op.add_column does not, so without
    # this the ALTER fails with UndefinedObject.
    _BAN_MODE.create(op.get_bind(), checkfirst=False)
    op.add_column(
        "instance_settings",
        sa.Column(
            "crowdsec_ban_mode",
            _BAN_MODE,
            nullable=False,
            server_default="megoopm",
        ),
    )
    op.add_column(
        "instance_settings",
        sa.Column("crowdsec_ban_page_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_instance_settings_crowdsec_ban_page_id_custom_pages"),
        "instance_settings",
        "custom_pages",
        ["crowdsec_ban_page_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_instance_settings_crowdsec_ban_page_id"),
        "instance_settings",
        ["crowdsec_ban_page_id"],
    )
    # Bare name: alembic applies the ck_%(table_name)s_%(constraint_name)s
    # convention, so an expanded name would be double-prefixed.
    op.create_check_constraint(
        "ban_custom_page_needs_page",
        "instance_settings",
        "crowdsec_ban_mode <> 'custom_page' OR crowdsec_ban_page_id IS NOT NULL",
    )


def downgrade() -> None:
    # The constraint goes first: dropping a column it references would fail.
    op.drop_constraint(
        op.f("ck_instance_settings_ban_custom_page_needs_page"),
        "instance_settings",
        type_="check",
    )
    op.drop_index(op.f("ix_instance_settings_crowdsec_ban_page_id"), "instance_settings")
    op.drop_constraint(
        op.f("fk_instance_settings_crowdsec_ban_page_id_custom_pages"),
        "instance_settings",
        type_="foreignkey",
    )
    op.drop_column("instance_settings", "crowdsec_ban_page_id")
    op.drop_column("instance_settings", "crowdsec_ban_mode")
    # drop_column leaves the type behind.
    _BAN_MODE.drop(op.get_bind(), checkfirst=True)
```

- [x] **Step 4: Run the migration up and back down**

```bash
docker exec megoopm-test sh -c "alembic upgrade head && alembic downgrade -1 && alembic upgrade head"
```

Expected: all three succeed. The round trip is the point — a downgrade that leaves the enum type behind makes the next upgrade fail with `DuplicateObjectError`, which is the failure this codebase has already hit once.

- [x] **Step 5: Write the failing API tests**

Append to `backend/tests/test_settings_api.py`. The file provides an
`AsyncClient` fixture named `client` and a bearer-header fixture named `auth`;
both are used below. Every test in that module is `async` and the module sets
`pytestmark = pytest.mark.asyncio`.

```python
async def test_ban_page_defaults_to_the_megoopm_document(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    """An upgraded install serves a real page without anyone opening Settings."""
    body = (await client.get("/api/v1/settings", headers=auth)).json()
    assert body["crowdsec_ban_mode"] == "megoopm"
    assert body["crowdsec_ban_page_id"] is None


async def test_ban_page_can_be_set_to_none(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    """The deliberate choice to keep today's bare 403."""
    resp = await client.patch(
        "/api/v1/settings/ban-page", json={"crowdsec_ban_mode": "none"}, headers=auth
    )
    assert resp.status_code == 200
    assert resp.json()["crowdsec_ban_mode"] == "none"


async def test_ban_page_custom_mode_requires_a_page(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    resp = await client.patch(
        "/api/v1/settings/ban-page",
        json={"crowdsec_ban_mode": "custom_page"},
        headers=auth,
    )
    assert resp.status_code == 422


async def test_ban_page_rejects_a_page_that_does_not_exist(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    resp = await client.patch(
        "/api/v1/settings/ban-page",
        json={"crowdsec_ban_mode": "custom_page", "crowdsec_ban_page_id": 999999},
        headers=auth,
    )
    assert resp.status_code == 422


async def test_switching_away_from_custom_page_clears_the_reference(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    """The stored row must always describe exactly one configuration."""
    page = (
        await client.post(
            "/api/v1/custom-pages",
            json={"name": "Blocked", "html": "<h1>no</h1>"},
            headers=auth,
        )
    ).json()
    await client.patch(
        "/api/v1/settings/ban-page",
        json={"crowdsec_ban_mode": "custom_page", "crowdsec_ban_page_id": page["id"]},
        headers=auth,
    )
    body = (
        await client.patch(
            "/api/v1/settings/ban-page",
            json={"crowdsec_ban_mode": "megoopm"},
            headers=auth,
        )
    ).json()
    assert body["crowdsec_ban_page_id"] is None
```

- [x] **Step 6: Run the tests to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_settings_api.py -p no:cacheprovider -k ban_page
```

Expected: FAIL — `KeyError: 'crowdsec_ban_mode'` and 404s for the unrouted path.

- [x] **Step 7: Add the schema fields**

In `backend/app/schemas/instance_settings.py`, add to `InstanceSettingsRead` (and to its `from_row`):

```python
    crowdsec_ban_mode: CrowdSecBanMode
    crowdsec_ban_page_id: int | None
```

and add the update model next to `InstanceSettingsUpdate`:

```python
class CrowdSecBanUpdate(BaseModel):
    """Set the CrowdSec ban page. ``crowdsec_ban_mode`` is required.

    Required for the same reason ``default_site_mode`` is on its sibling:
    "custom_page needs a page" cannot be checked against a payload that omits
    the mode, and a schema never sees the stored row.
    """

    crowdsec_ban_mode: CrowdSecBanMode
    crowdsec_ban_page_id: int | None = Field(
        default=None, description="Required when the mode is 'custom_page'"
    )

    @model_validator(mode="after")
    def _coherent(self) -> CrowdSecBanUpdate:
        """Mirror the database CHECK constraint, with a usable message."""
        if (
            self.crowdsec_ban_mode is CrowdSecBanMode.custom_page
            and self.crowdsec_ban_page_id is None
        ):
            raise ValueError(
                "crowdsec_ban_page_id is required when the mode is 'custom_page'"
            )
        return self
```

Import `CrowdSecBanMode` from `app.models.enums`.

- [x] **Step 8: Add the service function**

In `backend/app/services/instance_settings.py`, after `update_default_site`:

```python
async def update_ban_page(db: AsyncSession, changes: dict[str, Any]) -> InstanceSettings:
    """Apply a coherent ban-page payload, clearing the unused column."""
    row = await get_instance_settings(db)
    mode = changes["crowdsec_ban_mode"]

    row.crowdsec_ban_mode = mode
    row.crowdsec_ban_page_id = (
        changes.get("crowdsec_ban_page_id")
        if mode is CrowdSecBanMode.custom_page
        else None
    )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        # The only FK touched here is the custom page, so a violation means a
        # bogus id.
        raise UnknownCustomPageError(str(exc.orig)) from exc
    await db.refresh(row)
    return row
```

Import `CrowdSecBanMode` from `app.models.enums`.

- [x] **Step 9: Add the route**

In `backend/app/api/routes/settings.py`, after the `/default-site` route:

```python
@router.patch("/ban-page", response_model=InstanceSettingsRead)
async def update_ban_page_settings(
    body: CrowdSecBanUpdate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> InstanceSettingsRead:
    """Choose what a CrowdSec-blocked visitor is served. Admin-only.

    ``after_config_write``, not a bare audit: this changes a file nginx serves,
    so the config has to be rewritten and reloaded for the choice to take
    effect at all.
    """
    changes = body.model_dump()
    try:
        row = await settings_service.update_ban_page(db, changes)
    except settings_service.UnknownCustomPageError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="crowdsec_ban_page_id does not reference an existing custom page",
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.update,
        object_type="instance_settings",
        object_id=row.id,
        meta={"crowdsec_ban_mode": row.crowdsec_ban_mode.value},
    )
    return InstanceSettingsRead.from_row(row)
```

Import `CrowdSecBanUpdate` alongside the other schemas.

- [x] **Step 10: Run the tests and refresh the API contract**

```bash
docker exec megoopm-test python -m pytest tests/test_settings_api.py -p no:cacheprovider
docker exec megoopm-test python -m scripts.export_openapi
docker exec megoopm-test python -m pytest -p no:cacheprovider
docker exec megoopm-test ruff check app tests alembic
```

Expected: all pass, ruff clean. `test_committed_openapi_is_in_sync` passes only after the export.

- [x] **Step 11: Commit**

```bash
git add backend/app/models backend/alembic backend/app/schemas backend/app/services backend/app/api backend/tests backend/openapi.json
git commit -m "feat(crowdsec): store which page a blocked visitor is served"
```

---

### Task 2: Render the ban page

**Files:**
- Modify: `backend/app/services/nginx/state.py`
- Modify: `backend/app/services/nginx/loader.py`
- Modify: `backend/app/services/nginx/renderer.py`
- Create: `backend/app/templates/nginx/banned.html.j2`
- Test: `backend/tests/test_nginx_render.py`

**Interfaces:**
- Consumes: `CrowdSecBanMode` and the two columns from Task 1.
- Produces: `BanPageSpec(mode: str, html: str = "")` in `state.py`; `DesiredState.ban_page: BanPageSpec | None`; the key `megoopm-ban.html` in the mapping returned by `render_default_site(state)`.

- [x] **Step 1: Add the spec type**

In `backend/app/services/nginx/state.py`, after `DefaultSiteSpec`:

```python
@dataclass(frozen=True, slots=True)
class BanPageSpec:
    """What a CrowdSec-blocked visitor is served.

    ``html`` is already resolved for ``custom_page``: the loader reads the
    referenced document and puts it here, so the renderer never reaches into
    the database. For ``megoopm`` the renderer renders the shipped template and
    this stays empty — the same division ``DefaultSiteSpec`` makes between
    ``congratulations`` and ``custom_page``.
    """

    # One of CrowdSecBanMode's values, as a plain string.
    mode: str
    html: str = ""
```

and the field on `DesiredState`, after `default_tls`:

```python
    ban_page: BanPageSpec | None = None
```

- [x] **Step 2: Write the failing render tests**

Append to `backend/tests/test_nginx_render.py`, adding `BanPageSpec` to the state import block:

```python
def test_ban_page_writes_the_megoopm_document() -> None:
    files = render_default_site(DesiredState(ban_page=BanPageSpec(mode="megoopm")))
    assert "megoopm-ban.html" in files
    assert "<html" in files["megoopm-ban.html"].lower()


def test_ban_page_writes_the_referenced_custom_page() -> None:
    files = render_default_site(
        DesiredState(ban_page=BanPageSpec(mode="custom_page", html="<h1>Blocked</h1>"))
    )
    assert files["megoopm-ban.html"] == "<h1>Blocked</h1>"


def test_ban_page_none_writes_no_file_at_all() -> None:
    """An empty file would be served as a blank page with a 403; the bouncer
    guards on the file EXISTING, so the absence is what restores the bare 403."""
    files = render_default_site(DesiredState(ban_page=BanPageSpec(mode="none")))
    assert "megoopm-ban.html" not in files


def test_ban_page_custom_mode_with_a_missing_document_writes_no_file() -> None:
    """A blank white page reads as a broken deployment; the bare 403 does not."""
    files = render_default_site(
        DesiredState(ban_page=BanPageSpec(mode="custom_page", html=""))
    )
    assert "megoopm-ban.html" not in files


def test_the_megoopm_ban_document_leaks_nothing_about_the_decision() -> None:
    """It is static — the bouncer emits it verbatim — so anything specific in
    it would be a lie, and an IP or ban duration would help someone probing."""
    body = render_default_site(DesiredState(ban_page=BanPageSpec(mode="megoopm")))[
        "megoopm-ban.html"
    ]
    for leak in ("{{", "%s", "duration", "your ip"):
        assert leak not in body.lower()


def test_a_default_site_and_a_ban_page_coexist_in_one_directory() -> None:
    """They share a reconciliation target; neither may displace the other."""
    files = render_default_site(
        DesiredState(
            default_site=DefaultSiteSpec(mode="not_found"),
            ban_page=BanPageSpec(mode="megoopm"),
        )
    )
    assert {"megoopm-default.conf", "megoopm-ban.html"} <= set(files)
```

Add `render_default_site` and `DefaultSiteSpec` to the imports at the top of the file if they are not already there.

- [x] **Step 3: Run the tests to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_nginx_render.py -p no:cacheprovider -k ban
```

Expected: FAIL — `ImportError` on `BanPageSpec` until Step 1 is applied, then `KeyError: 'megoopm-ban.html'`.

- [x] **Step 4: Write the shipped page**

Create `backend/app/templates/nginx/banned.html.j2`. It is emitted verbatim by the bouncer, so it must be a complete standalone document with no external references. Match `congratulations.html.j2`'s palette and its light/dark handling — open that file and reuse its `:root` / `prefers-color-scheme` block rather than inventing a second palette:

```jinja
{# Served to a CrowdSec-blocked visitor. Read once at nginx init and emitted
   verbatim, so: no Jinja placeholders survive to runtime, no request data is
   available, and nothing here may describe the visitor, the decision that
   matched, or how long the block lasts — all three help someone probing the
   instance.

   The palette is the same one congratulations.html.j2 uses, so the two pages
   read as one product. Each colour is declared twice on purpose: the hex is
   the fallback for engines without oklch(), and the oklch line wins where it
   is supported. #}
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Access blocked</title>
    <style>
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
          --glow: 0 0 24px rgba(79, 220, 239, 0.35);
        }
      }

      * {
        box-sizing: border-box;
      }

      body {
        margin: 0;
        min-height: 100dvh;
        display: grid;
        place-items: center;
        padding: 1.5rem;
        background: var(--bg);
        color: var(--fg);
        font: 16px/1.6 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
      }

      main {
        max-width: 32rem;
        width: 100%;
        padding: 2.5rem 2rem;
        text-align: center;
        background: var(--card);
        border: 1px solid var(--border);
        border-radius: 16px;
        box-shadow: var(--glow);
      }

      h1 {
        margin: 0 0 0.75rem;
        font-size: 1.75rem;
        letter-spacing: -0.01em;
        color: var(--accent);
      }

      p {
        margin: 0 0 0.5rem;
        color: var(--muted);
      }

      p:last-child {
        margin-bottom: 0;
      }
    </style>
  </head>
  <body>
    <main>
      <h1>Access blocked</h1>
      <p>Your request was blocked by this site's security policy.</p>
      <p>If you believe this is a mistake, contact the site operator.</p>
    </main>
  </body>
</html>
```

- [x] **Step 5: Emit the file**

In `backend/app/services/nginx/renderer.py`, add next to `DEFAULT_SITE_HTML`:

```python
BAN_PAGE_HTML = "megoopm-ban.html"
```

and inside `render_default_site`, before the `return`, after the default-site files are built:

```python
    # Same directory, therefore the same reconciliation target. Mode "none" —
    # and a custom page whose document has gone missing — emit no key at all:
    # ban.lua guards on the file EXISTING, so the absence is what restores the
    # bare 403. An empty file would serve a blank page instead.
    ban = state.ban_page
    if ban is not None and ban.mode != "none":
        body = (
            _env().get_template("banned.html.j2").render()
            if ban.mode == "megoopm"
            else ban.html
        )
        if body:
            files[BAN_PAGE_HTML] = body
```

Note `render_default_site` returns early with `{}` when `state.default_site is None`; move that guard so the ban page is still written when no default site is configured — the two settings are independent.

- [x] **Step 6: Resolve the mode in the loader**

In `backend/app/services/nginx/loader.py`, add next to `_load_default_site`:

```python
async def _load_ban_page(session: AsyncSession) -> BanPageSpec | None:
    """Read the ban-page setting, resolving a referenced page into its HTML.

    Dereferenced here for the same reason the default site is: the renderer
    stays a pure function of explicit data.
    """
    row = await session.get(InstanceSettings, 1)
    if row is None:
        return None

    html = ""
    if row.crowdsec_ban_mode is CrowdSecBanMode.custom_page and row.crowdsec_ban_page_id:
        page = await session.get(CustomPage, row.crowdsec_ban_page_id)
        # The FK is RESTRICT, so a missing page means the row was edited outside
        # the API. Leaving html empty makes the renderer write no file, which
        # degrades to the bare 403 rather than to a blank white page.
        html = page.html if page is not None else ""

    return BanPageSpec(mode=row.crowdsec_ban_mode.value, html=html)
```

Import `BanPageSpec` and `CrowdSecBanMode`. Then call it in `load_desired_state` beside `default_site = await _load_default_site(session)`:

```python
    ban_page = await _load_ban_page(session)
```

and add `ban_page=ban_page` to the `DesiredState(...)` construction.

- [x] **Step 7: Cover the loader against real rows**

The renderer tests above cover the modes, but nothing yet proves the loader
dereferences a chosen page. Create
`backend/tests/test_ban_page_loader_pg.py`, reusing the `pg_session` fixture
pattern from `tests/test_nginx_default_tls_pg.py` verbatim:

```python
async def test_the_chosen_page_is_dereferenced(pg_session: AsyncSession) -> None:
    page = CustomPage(name="Blocked", html="<h1>Blocked</h1>")
    pg_session.add(page)
    await pg_session.flush()
    pg_session.add(
        InstanceSettings(
            id=1,
            default_site_mode=DefaultSiteMode.not_found,
            crowdsec_ban_mode=CrowdSecBanMode.custom_page,
            crowdsec_ban_page_id=page.id,
        )
    )
    await pg_session.flush()

    state = await load_desired_state(pg_session, certs_dir="/data/certs")

    assert state.ban_page is not None
    assert state.ban_page.mode == "custom_page"
    assert state.ban_page.html == "<h1>Blocked</h1>"


async def test_the_megoopm_mode_needs_no_document(pg_session: AsyncSession) -> None:
    # The renderer supplies the document; the loader must not invent one.
    pg_session.add(
        InstanceSettings(
            id=1,
            default_site_mode=DefaultSiteMode.not_found,
            crowdsec_ban_mode=CrowdSecBanMode.megoopm,
        )
    )
    await pg_session.flush()

    state = await load_desired_state(pg_session, certs_dir="/data/certs")

    assert state.ban_page.mode == "megoopm"
    assert state.ban_page.html == ""
```

- [x] **Step 8: Run the tests**

```bash
docker exec megoopm-test python -m pytest -p no:cacheprovider
docker exec megoopm-test ruff check app tests alembic
```

Expected: all pass, ruff clean.

- [x] **Step 9: Commit**

```bash
git add backend/app/services/nginx backend/app/templates/nginx/banned.html.j2 backend/tests/test_nginx_render.py
git commit -m "feat(crowdsec): render the chosen ban page beside the default site"
```

---

### Task 3: Point the bouncer at it

**Files:**
- Modify: `infra/nginx/crowdsec-bouncer.conf`
- Modify: `docs/crowdsec.md`

**Interfaces:**
- Consumes: the file `megoopm-ban.html` written by Task 2 into the default directory.

- [x] **Step 1: Set the template path**

In `infra/nginx/crowdsec-bouncer.conf`, under the `FALLBACK_REMEDIATION` block:

```
# The ban page MegooPM writes, chosen under Settings. A fixed path, because the
# PRESENCE of this file is the switch: ban.lua guards with file_exist() and
# falls back to a bare 403 when it is missing, so the "no page" mode needs no
# handling here. envsubst runs with an explicit variable list, so this literal
# path passes through untouched.
#
# Read once when the configuration loads. init_by_lua re-runs on `nginx -s
# reload`, so the normal apply path picks up a new page — no restart needed.
BAN_TEMPLATE_PATH=/data/nginx/default/megoopm-ban.html
```

- [x] **Step 2: Verify nginx still loads with the setting present**

```bash
export MSYS_NO_PATHCONV=1
docker run --rm --entrypoint sh \
  -v "/c/Projects/megoopm/infra/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" \
  -v "/c/Projects/megoopm/infra/nginx/crowdsec-bouncer.conf:/etc/nginx/crowdsec-bouncer.conf:ro" \
  megoopm-nginx:latest -c '
mkdir -p /data/nginx/conf.d /data/nginx/default /var/empty/megoopm
echo "access_log /dev/null;" > /etc/nginx/logging.conf
openresty -p /usr/local/openresty/nginx -c /etc/nginx/nginx.conf -t 2>&1 | tail -3
'
```

Expected: `syntax is ok` / `test is successful`. The mounted conf is the raw template — `${CROWDSEC_LAPI_URL}` is unsubstituted, so a bouncer init error in the output is expected and not a failure of this step; what matters is that nginx loads.

- [x] **Step 3: Check the file line endings**

```bash
git ls-files --eol infra/nginx/crowdsec-bouncer.conf
```

Expected: `i/lf w/lf`. A CRLF config file is read by Lua as values with a trailing `\r`, which would make the path not exist and silently disable the page.

- [x] **Step 4: Update the docs**

In `docs/crowdsec.md`, amend the verification step that says "expect `403` from the bouncer" to note that the response now carries the configured ban page by default, and that a bare 403 means either the `none` mode or a template that failed to load.

- [x] **Step 5: Commit**

```bash
git add infra/nginx/crowdsec-bouncer.conf docs/crowdsec.md
git commit -m "feat(crowdsec): serve the managed ban page to blocked visitors"
```

---

### Task 4: The Settings card

**Files:**
- Create: `frontend/src/components/settings/ban-page-card.tsx`
- Create: `frontend/src/components/settings/ban-page-card.test.tsx`
- Modify: `frontend/src/components/settings/settings-view.tsx`

**Interfaces:**
- Consumes: `PATCH /api/v1/settings/ban-page` and the regenerated `InstanceSettings` type from Task 1.
- Produces: `<BanPageCard settings={row} pages={pages} onSaved={setRow} />`

- [x] **Step 1: Regenerate the API types**

```bash
cd frontend && npm run gen:api
```

Expected: `crowdsec_ban_mode` and `crowdsec_ban_page_id` appear in the generated types.

- [x] **Step 2: Write the failing tests**

Create `frontend/src/components/settings/ban-page-card.test.tsx`, mocking `instanceSettings` as `llm-card.test.tsx` does:

```tsx
it("offers the page dropdown only for the custom-page mode", async () => {
  const user = userEvent.setup();
  render(<BanPageCard settings={makeSettings()} pages={[makePage()]} onSaved={vi.fn()} />);

  expect(screen.queryByLabelText("Page to serve")).not.toBeInTheDocument();
  await user.click(screen.getByRole("radio", { name: /custom page/i }));
  expect(await screen.findByLabelText("Page to serve")).toBeInTheDocument();
});

it("saves the chosen mode", async () => {
  const update = vi.spyOn(instanceSettings, "updateBanPage").mockResolvedValue(makeSettings());
  const user = userEvent.setup();
  render(<BanPageCard settings={makeSettings()} pages={[]} onSaved={vi.fn()} />);

  await user.click(screen.getByRole("radio", { name: /no page/i }));
  await user.click(screen.getByRole("button", { name: "Save ban page" }));

  expect(update).toHaveBeenCalledWith({ crowdsec_ban_mode: "none", crowdsec_ban_page_id: null });
});

it("says an edit to the chosen page needs a config change to take effect", async () => {
  // Otherwise an operator edits the page, sees no change, and assumes a bug.
  const user = userEvent.setup();
  render(<BanPageCard settings={makeSettings()} pages={[makePage()]} onSaved={vi.fn()} />);

  await user.click(screen.getByRole("radio", { name: /custom page/i }));

  expect(await screen.findByText(/takes effect on the next/i)).toBeInTheDocument();
});
```

- [x] **Step 3: Run the tests to verify they fail**

```bash
cd frontend && npx vitest run src/components/settings/ban-page-card.test.tsx
```

Expected: FAIL — the module does not exist.

- [x] **Step 4: Build the card**

First add the client call in `frontend/src/lib/api.ts`, beside the existing
default-site and LLM calls (match their exact style — this repo wraps `fetch`
in a shared helper, so copy the neighbouring method rather than calling `fetch`
directly):

```ts
  updateBanPage: (body: {
    crowdsec_ban_mode: string;
    crowdsec_ban_page_id: number | null;
  }) => patch<InstanceSettings>("/settings/ban-page", body),
```

Then create `frontend/src/components/settings/ban-page-card.tsx`:

```tsx
"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Radio, RadioGroup } from "@/components/ui/radio-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { instanceSettings, type CustomPage, type InstanceSettings } from "@/lib/api";
import { describeError } from "@/lib/errors";

export function BanPageCard({
  settings,
  pages,
  onSaved,
}: {
  settings: InstanceSettings;
  pages: CustomPage[];
  onSaved: (row: InstanceSettings) => void;
}) {
  const [mode, setMode] = useState(settings.crowdsec_ban_mode);
  const [pageId, setPageId] = useState<number | null>(settings.crowdsec_ban_page_id);
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    setSaving(true);
    try {
      const row = await instanceSettings.updateBanPage({
        crowdsec_ban_mode: mode,
        // The API clears this itself for the other modes; sending null keeps
        // the payload describing exactly one configuration.
        crowdsec_ban_page_id: mode === "custom_page" ? pageId : null,
      });
      onSaved(row);
      toast.success("Ban page saved");
    } catch (error) {
      toast.error(describeError(error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="space-y-4 rounded-xl border p-4">
      <div>
        <h3 className="text-sm font-semibold">Ban page</h3>
        <p className="text-muted-foreground text-sm">
          What a visitor blocked by CrowdSec is served.
        </p>
      </div>

      <RadioGroup
        value={mode}
        onValueChange={(value) => {
          setMode(value as typeof mode);
          // Reset here rather than in an effect: this is the transition that
          // invalidates the selection, and eslint forbids setState in effects.
          if (value !== "custom_page") setPageId(null);
        }}
      >
        <Radio value="megoopm" label="MegooPM page" />
        <Radio value="custom_page" label="Custom page" />
        <Radio value="none" label="No page (403)" />
      </RadioGroup>

      {mode === "custom_page" ? (
        <div className="space-y-1.5">
          <Label htmlFor="ban-page">Page to serve</Label>
          <Select
            value={pageId === null ? "" : String(pageId)}
            onValueChange={(value) => setPageId(Number(value))}
          >
            <SelectTrigger id="ban-page" disabled={saving}>
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
          <p className="text-muted-foreground text-xs">
            Editing the page itself takes effect on the next configuration change.
          </p>
        </div>
      ) : null}

      <Button
        onClick={handleSave}
        disabled={saving || (mode === "custom_page" && pageId === null)}
      >
        {saving ? "Saving…" : "Save ban page"}
      </Button>
    </section>
  );
}
```

Two details to check against the repo before running: the radio-group and
select primitives' exact prop shapes (open the default-site card in
`settings-view.tsx` and mirror what it does), and whether the error helper is
named `describeError` — `llm-card.tsx` shows the convention in use. The button
label `Save ban page` must stay distinct from the other cards' save buttons, or
the page has two controls with the same accessible name.

- [x] **Step 5: Mount it**

In `settings-view.tsx`, beside the existing `LlmCard`:

```tsx
{row ? <BanPageCard settings={row} pages={pages} onSaved={setRow} /> : null}
```

The view already loads `customPages.list()` for the default-site dropdown, so reuse that state rather than fetching again.

- [x] **Step 6: Run the full frontend gate**

```bash
cd frontend && npx vitest run && npm run typecheck && npm run lint && npm run build
```

Expected: all pass.

- [x] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat(crowdsec): choose the ban page from Settings"
```

---

## Manual verification

Not reachable by any automated test, and worth doing once against a running stack:

1. Ban your own IP: `docker exec megoopm-crowdsec cscli decisions add --ip <your-ip> --duration 5m`.
2. Visit any CrowdSec-enabled host. Expect the MegooPM ban page with status `403`.
3. Check the nginx log no longer carries `BAN_TEMPLATE_PATH and REDIRECT_LOCATION variable are empty`.
4. Switch the setting to a Custom Page, save, and reload the blocked page. Expect that page's HTML.
5. Switch to `No page`, save, reload. Expect the bare 403 again, and confirm `megoopm-ban.html` is gone from the default directory.
6. `cscli decisions delete --ip <your-ip>` to unblock.

Step 5 is the one most worth doing: it exercises the file-deletion path that makes "no page" work, which nothing else verifies end to end.


---

## Executed 2026-09-02

All four tasks complete. Backend **723 passed, 41 skipped**, ruff clean.
Frontend **401 passed, 1 skipped**, typecheck, lint and build clean.

Four things the plan did not anticipate:

- **Running the migration polluted the shared test database.** Step 4's
  `alembic upgrade head` leaves a seeded `instance_settings` row, and the API
  fixtures then fail on a duplicate primary key. Reset with
  `docker exec megoopm-testdb psql -U megoopm -d megoopm -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"`
  before running the suite.
- **Adding two required fields to `InstanceSettingsRead` broke every existing
  frontend fixture** that constructs one — four test files. `vitest` passed
  throughout; only `npm run typecheck` caught it.
- **Both radio groups needed names.** The page now has two cards each offering
  a "Custom page" radio, so an unscoped query matched two elements — and a
  screen reader had the same problem. Both `RadioGroup`s now carry an
  `aria-label`, and the default-site tests scope to theirs.
- **The ban card's radios must not set `aria-label`.** base-ui's
  `aria-labelledby` from the wrapping `<label>` wins over it, so setting both
  made the accessible name the label twice over plus the hint.

Task 3 verified that the literal `BAN_TEMPLATE_PATH` survives the entrypoint's
`envsubst` untouched and that nginx loads cleanly with it set. That a banned IP
is actually served the document still needs a live LAPI decision — see the
manual verification below, which remains unrun.
