# Maintenance Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator mark a proxy host under maintenance, so every visitor outside an allow-list gets a branded 503 page while the operator can still reach the real site.

**Architecture:** A per-host `geo` block names the addresses that skip maintenance; every generated location gains `if ($mgm_maint_<id>) { return 503; }`; a server-level `error_page 503` — emitted **before** the errors include — points at a shared `megoopm-maintenance.html` rendered from the same palette and logo as the error pages. The page is chosen once instance-wide, exactly like the ban page.

**Tech Stack:** FastAPI, SQLAlchemy + Alembic, Jinja2 nginx templates, OpenResty 1.25.3.2, Next.js 16 / React 19, vitest.

**Spec:** `docs/superpowers/specs/2026-09-06-maintenance-mode-design.md`

## Global Constraints

- **Backend tests cannot run on Windows** (`app/services/cluster/locks.py` imports `fcntl`). Use the throwaway container, and Postgres for anything touching `proxy_hosts` (it has `ARRAY` columns the SQLite fixture cannot hold):
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
  docker exec megoopm-test pip install -q "pytest>=8.2" "pytest-asyncio>=0.23" "aiosqlite>=0.20" \
    "ruff>=0.6" maxminddb "webauthn>=3.0" "cbor2>=5.6"
  ```
  Run pytest **without** `-q` (`pyproject.toml` already sets it).
- **Frontend formatting is not uniform.** Some files are wrapped at 100 columns, some at 80, some at neither. Before running prettier on a file, check what it already satisfies (`npx prettier --check --print-width 100 <file>`); if it satisfies neither, hand-match the surrounding style. Always read `git diff --stat` before committing — a one-line change reporting 50 changed lines is formatting churn, not your edit.
- **Ordering is load-bearing.** The maintenance `error_page 503` must be emitted **before** `include {{ default_dir }}/{{ errors_conf }}`. At one configuration level the *first* `error_page` for a status wins; reversed, the branded 503 is served and maintenance silently does nothing.
- **The ACME challenge location is never guarded.** `^~ /.well-known/acme-challenge/` must stay reachable or a host left under maintenance fails its certificate renewal.
- **The branded page names no host, upstream, path or request data, and makes no external request.** It is reachable by anyone. The logo is an inline base64 `data:` URI, as in the error pages.
- **`write_whitelist_file`-style in-place writes do not apply here** — documents in the default dir are written by the existing `apply_state` reconciliation; do not add new file-writing code.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  ```

---

### Task 1: Storage

**Files:**
- Modify: `backend/app/models/enums.py`, `backend/app/models/proxy_host.py`, `backend/app/models/instance_settings.py`
- Create: `backend/alembic/versions/0035_maintenance_mode.py`
- Test: `backend/tests/test_maintenance_migration.py` (create)

**Interfaces:**
- Produces: `MaintenancePageMode` (`megoopm` | `custom_page`); `ProxyHost.maintenance_enabled: bool`, `ProxyHost.maintenance_allow: list[str]`; `InstanceSettings.maintenance_mode`, `.maintenance_page_id: int | None`, `.maintenance_retry_after_minutes: int`.

- [ ] **Step 1: Write the failing migration test**

Create `backend/tests/test_maintenance_migration.py`:

```python
"""The 0035 columns, against a real Postgres.

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

SCHEMA = "maintenance_probe"
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


def test_a_host_is_not_under_maintenance_by_default(migrated) -> None:
    migrated("0035_maintenance_mode")
    asyncio.run(
        _exec(
            [
                "INSERT INTO upstreams (id, name, lb_method, context, enabled)"
                " VALUES (1, 'pool-a', 'round_robin', 'http', true)",
                "INSERT INTO proxy_hosts (id, domain_names, upstream_id, forward_scheme, enabled)"
                " VALUES (1, ARRAY['a.example.com'], 1, 'http', true)",
            ]
        )
    )

    rows = asyncio.run(
        _exec(["SELECT maintenance_enabled, maintenance_allow FROM proxy_hosts WHERE id = 1"])
    )

    # Switching a feature on must never be something an upgrade does for you.
    assert [tuple(r) for r in rows] == [(False, [])]


def test_the_allow_list_holds_addresses(migrated) -> None:
    migrated("0035_maintenance_mode")
    asyncio.run(
        _exec(
            [
                "INSERT INTO upstreams (id, name, lb_method, context, enabled)"
                " VALUES (1, 'pool-a', 'round_robin', 'http', true)",
                "INSERT INTO proxy_hosts (id, domain_names, upstream_id, forward_scheme, enabled,"
                " maintenance_enabled, maintenance_allow)"
                " VALUES (1, ARRAY['a.example.com'], 1, 'http', true, true,"
                " ARRAY['203.0.113.5', '10.0.0.0/8'])",
            ]
        )
    )

    rows = asyncio.run(_exec(["SELECT maintenance_allow FROM proxy_hosts WHERE id = 1"]))

    assert [tuple(r) for r in rows] == [(["203.0.113.5", "10.0.0.0/8"],)]


def test_the_settings_default_to_the_megoopm_page(migrated) -> None:
    migrated("0035_maintenance_mode")

    rows = asyncio.run(
        _exec(
            [
                "SELECT maintenance_mode, maintenance_page_id,"
                " maintenance_retry_after_minutes FROM instance_settings WHERE id = 1"
            ]
        )
    )

    # A host switched into maintenance must have something to serve on day one.
    assert [tuple(r) for r in rows] == [("megoopm", None, 60)]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_maintenance_migration.py -p no:cacheprovider -p no:warnings`
Expected: FAIL — `0035_maintenance_mode` does not exist.

- [ ] **Step 3: Add the enum**

In `backend/app/models/enums.py`, beside `CrowdSecBanMode`:

```python
class MaintenancePageMode(enum.StrEnum):
    """What a visitor sees while a host is under maintenance.

    No ``none``: a host under maintenance must answer *something*. A bare 503
    with no body tells a visitor nothing and tells a crawler nothing about
    whether to come back.
    """

    megoopm = "megoopm"
    custom_page = "custom_page"
```

- [ ] **Step 4: Add the model columns**

In `backend/app/models/proxy_host.py`, on `ProxyHost`, after `crowdsec_appsec_enabled`:

```python
    # Maintenance (planned downtime): every visitor outside maintenance_allow
    # is served the maintenance page with a 503, so crawlers keep the site's
    # rankings and monitoring sees a real outage.
    maintenance_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    #: IPs and CIDRs that reach the real site anyway, so the operator can
    #: verify a deploy before switching maintenance off.
    maintenance_allow: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, default=list, server_default="{}"
    )
```

`ARRAY` and `String` are already imported in that module (`domain_names` uses both).

In `backend/app/models/instance_settings.py`, after the CrowdSec ban page columns:

```python
    maintenance_mode: Mapped[MaintenancePageMode] = mapped_column(
        Enum(
            MaintenancePageMode,
            name="maintenance_page_mode",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=MaintenancePageMode.megoopm,
        server_default=MaintenancePageMode.megoopm.value,
    )
    # RESTRICT, like the ban page: a page in use cannot be deleted.
    maintenance_page_id: Mapped[int | None] = mapped_column(
        ForeignKey("custom_pages.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    #: Emitted as Retry-After. Configurable because a fixed guess is wrong for
    #: someone, and the alternative is editing advanced config.
    maintenance_retry_after_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60, server_default="60"
    )
```

Add `MaintenancePageMode` to that module's `from app.models.enums import ...` line.

- [ ] **Step 5: Write the migration**

Create `backend/alembic/versions/0035_maintenance_mode.py`:

```python
"""Maintenance mode: a proxy host that answers "we'll be back"

Two shapes at once. On ``proxy_hosts``, the per-host switch and the
addresses that skip it. On ``instance_settings``, the page every host under
maintenance serves, chosen once like the ban page.

The enum is created by ``sa.Enum(...).create`` before the column that uses
it: ``op.add_column`` does not emit CREATE TYPE, unlike ``create_table``.

Revision ID: 0035_maintenance_mode
Revises: 0034_capi_credential_health
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035_maintenance_mode"
down_revision: str | None = "0034_capi_credential_health"
branch_labels: str | None = None
depends_on: str | None = None

_MODE = sa.Enum("megoopm", "custom_page", name="maintenance_page_mode")


def upgrade() -> None:
    _MODE.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "proxy_hosts",
        sa.Column(
            "maintenance_enabled", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.add_column(
        "proxy_hosts",
        sa.Column(
            "maintenance_allow",
            postgresql.ARRAY(sa.String(length=64)),
            nullable=False,
            server_default="{}",
        ),
    )

    op.add_column(
        "instance_settings",
        sa.Column("maintenance_mode", _MODE, nullable=False, server_default="megoopm"),
    )
    op.add_column(
        "instance_settings", sa.Column("maintenance_page_id", sa.BigInteger(), nullable=True)
    )
    op.create_index(
        "ix_instance_settings_maintenance_page_id",
        "instance_settings",
        ["maintenance_page_id"],
    )
    op.create_foreign_key(
        "fk_instance_settings_maintenance_page_id",
        "instance_settings",
        "custom_pages",
        ["maintenance_page_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "instance_settings",
        sa.Column(
            "maintenance_retry_after_minutes",
            sa.Integer(),
            nullable=False,
            server_default="60",
        ),
    )


def downgrade() -> None:
    op.drop_column("instance_settings", "maintenance_retry_after_minutes")
    op.drop_constraint(
        "fk_instance_settings_maintenance_page_id", "instance_settings", type_="foreignkey"
    )
    op.drop_index("ix_instance_settings_maintenance_page_id", table_name="instance_settings")
    op.drop_column("instance_settings", "maintenance_page_id")
    op.drop_column("instance_settings", "maintenance_mode")
    op.drop_column("proxy_hosts", "maintenance_allow")
    op.drop_column("proxy_hosts", "maintenance_enabled")
    # add_column never created it, so nothing else will drop it.
    _MODE.drop(op.get_bind(), checkfirst=True)
```

- [ ] **Step 6: Run the tests**

Run: `docker exec megoopm-test python -m pytest tests/test_maintenance_migration.py -p no:cacheprovider -p no:warnings`
Expected: 3 PASS.

- [ ] **Step 7: Run the full backend suite**

Run: `docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings`
Expected: all pass. If `tests/test_openapi.py` fails, that is Task 4's job — note it and continue only if nothing else fails.

- [ ] **Step 8: Commit**

```bash
git add backend/app/models backend/alembic backend/tests/test_maintenance_migration.py
git commit -m "feat(maintenance): storage for the per-host switch and the shared page

The switch and its bypass list live on the host; the page every host
under maintenance serves is chosen once, like the ban page. No 'none'
mode: a host under maintenance must answer something.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: The branded page

**Files:**
- Create: `backend/app/templates/nginx/maintenance.html.j2`
- Modify: `backend/app/services/nginx/state.py`, `backend/app/services/nginx/renderer.py`
- Test: `backend/tests/test_maintenance_render.py` (create)

**Interfaces:**
- Consumes: `MaintenancePageMode` from Task 1.
- Produces: `MaintenanceSpec(mode: str, html: str = "", retry_after_minutes: int = 60)` in `state.py`; `DesiredState.maintenance: MaintenanceSpec | None = None`; `ProxyHostSpec.maintenance_enabled: bool = False`, `.maintenance_allow: tuple[str, ...] = ()`; `MAINTENANCE_HTML = "megoopm-maintenance.html"` in `renderer.py`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_maintenance_render.py`:

```python
"""The maintenance document: written when needed, swept when not."""

from __future__ import annotations

from app.services.nginx.renderer import MAINTENANCE_HTML, render_default_site
from app.services.nginx.state import (
    BackendSpec,
    DesiredState,
    MaintenanceSpec,
    ProxyHostSpec,
    UpstreamSpec,
)


def _pool(id: int = 1) -> UpstreamSpec:
    return UpstreamSpec(id=id, name=f"pool-{id}", backends=(BackendSpec(host="10.0.0.5", port=80),))


def _host(**over) -> ProxyHostSpec:
    return ProxyHostSpec(id=1, domain_names=("app.example.com",), upstream_id=1, **over)


def test_no_host_under_maintenance_writes_no_document() -> None:
    # The shared directory is reconciled by prefix, so an unrendered file is
    # swept. Writing one nobody serves would leave a page behind for good.
    files = render_default_site(
        DesiredState(
            proxy_hosts=(_host(),),
            http_upstreams=(_pool(),),
            maintenance=MaintenanceSpec(mode="megoopm"),
        )
    )
    assert MAINTENANCE_HTML not in files


def test_the_shipped_page_is_written_for_a_host_under_maintenance() -> None:
    files = render_default_site(
        DesiredState(
            proxy_hosts=(_host(maintenance_enabled=True),),
            http_upstreams=(_pool(),),
            maintenance=MaintenanceSpec(mode="megoopm"),
        )
    )

    page = files[MAINTENANCE_HTML]
    assert "<!doctype html>" in page.lower()
    # Its own message, not the 503's: planned work, not a failure.
    assert "maintenance" in page.lower()


def test_a_custom_page_is_used_verbatim() -> None:
    files = render_default_site(
        DesiredState(
            proxy_hosts=(_host(maintenance_enabled=True),),
            http_upstreams=(_pool(),),
            maintenance=MaintenanceSpec(mode="custom_page", html="<h1>Back soon</h1>"),
        )
    )
    assert files[MAINTENANCE_HTML] == "<h1>Back soon</h1>"


def test_the_page_makes_no_external_request() -> None:
    """It is reachable by anyone and served when things are already broken.

    A webfont or a CDN script would leave it half-rendered exactly when it
    matters, and would tell a third party who is visiting.
    """
    files = render_default_site(
        DesiredState(
            proxy_hosts=(_host(maintenance_enabled=True),),
            http_upstreams=(_pool(),),
            maintenance=MaintenanceSpec(mode="megoopm"),
        )
    )
    page = files[MAINTENANCE_HTML]

    assert "http://" not in page
    assert "https://" not in page
    for src in page.split('src="')[1:]:
        assert src.startswith("data:")


def test_the_page_names_nothing_about_the_instance() -> None:
    # Anyone can see it, and naming a backend tells a prober how this is built.
    files = render_default_site(
        DesiredState(
            proxy_hosts=(_host(maintenance_enabled=True),),
            http_upstreams=(_pool(),),
            maintenance=MaintenanceSpec(mode="megoopm"),
        )
    )
    page = files[MAINTENANCE_HTML]

    assert "app.example.com" not in page
    assert "10.0.0.5" not in page
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_maintenance_render.py -p no:cacheprovider -p no:warnings`
Expected: FAIL — `MaintenanceSpec` and `MAINTENANCE_HTML` do not exist.

- [ ] **Step 3: Add the specs**

In `backend/app/services/nginx/state.py`, beside `BanPageSpec`:

```python
@dataclass(frozen=True, slots=True)
class MaintenanceSpec:
    """What a host under maintenance serves, instance-wide.

    ``html`` is already resolved for ``custom_page``: the loader reads the
    referenced document and puts it here, so the renderer never reaches into
    the database — the same division :class:`BanPageSpec` makes.
    """

    #: One of MaintenancePageMode's values, as a plain string.
    mode: str
    html: str = ""
    #: Emitted as Retry-After, in minutes.
    retry_after_minutes: int = 60
```

On `ProxyHostSpec`, after `crowdsec_appsec_enabled`:

```python
    # Maintenance: everyone outside maintenance_allow gets the page and a 503.
    maintenance_enabled: bool = False
    maintenance_allow: tuple[str, ...] = ()
```

On `DesiredState`, beside `ban_page`:

```python
    maintenance: MaintenanceSpec | None = None
```

Add `MaintenanceSpec` to that module's `__all__`.

- [ ] **Step 4: Write the template**

Create `backend/app/templates/nginx/maintenance.html.j2`, mirroring
`error.html.j2` — include `_palette.css.j2`, the logo `<img>` with
`src="{{ logo_data_uri }}"`, and this copy:

```jinja
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Down for maintenance</title>
    <style>
{% include "_palette.css.j2" %}
      * { box-sizing: border-box; }
      body {
        margin: 0; min-height: 100vh; display: grid; place-items: center;
        background: var(--bg); color: var(--fg); padding: 2rem;
        font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
      }
      .card { max-width: 34rem; text-align: center; }
      .logo { width: 64px; height: 64px; filter: var(--logo-glow); }
      h1 { margin: 1.25rem 0 0; font-size: 1.75rem; letter-spacing: -0.01em; }
      .rule {
        height: 2px; margin: 1.25rem auto; width: 6rem; border-radius: 2px;
        background: linear-gradient(90deg, transparent, var(--primary), transparent);
      }
      p.detail { margin: 0; color: var(--muted); line-height: 1.6; }
    </style>
  </head>
  <body>
    <div class="card">
      <img class="logo" src="{{ logo_data_uri }}" alt="" />
      <h1>Down for maintenance</h1>
      <div class="rule"></div>
      <p class="detail">
        We&rsquo;re making some planned improvements and will be back shortly.
        Thanks for your patience.
      </p>
    </div>
  </body>
</html>
```

Copy the exact `.logo`, `.rule` and `.card` rules from
`backend/app/templates/nginx/error.html.j2` if they have drifted from the
above — the two pages must look like siblings.

- [ ] **Step 5: Render it**

In `backend/app/services/nginx/renderer.py`, beside `BAN_PAGE_HTML`:

```python
MAINTENANCE_HTML = "megoopm-maintenance.html"
```

In `render_default_site`, after the ban-page block and before the return:

```python
    # Only when something serves it: this directory is reconciled by prefix,
    # so a document nobody references would otherwise be left behind for good.
    if any(host.maintenance_enabled for host in state.proxy_hosts):
        spec = state.maintenance
        body = ""
        if spec is not None:
            body = (
                _env().get_template("maintenance.html.j2").render()
                if spec.mode == "megoopm"
                else spec.html
            )
        # A custom page that has gone missing falls back to the shipped one:
        # an empty maintenance page is worse than a generic one.
        files[MAINTENANCE_HTML] = body or _env().get_template("maintenance.html.j2").render()
```

Add `MAINTENANCE_HTML` to the module's `__all__`.

- [ ] **Step 6: Run the tests**

Run: `docker exec megoopm-test python -m pytest tests/test_maintenance_render.py -p no:cacheprovider -p no:warnings`
Expected: 5 PASS.

- [ ] **Step 7: Check the existing default-site tests still hold**

Run: `docker exec megoopm-test python -m pytest tests/test_default_site_render.py -p no:cacheprovider -p no:warnings`
Expected: PASS. Those use `assert {...} <= set(files)` subset checks, so a new file does not break them.

- [ ] **Step 8: Commit**

```bash
git add backend/app/templates backend/app/services/nginx backend/tests/test_maintenance_render.py
git commit -m "feat(maintenance): the branded page, and where it is written

Its own document and its own copy rather than a binding onto the branded
503: 'we are doing planned work' and 'an upstream failed' are different
messages, and one page cannot say both well.

Written only while some host is under maintenance, because the shared
directory is reconciled by prefix and an unreferenced file would be left
behind for good.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: The server block

The task the whole feature turns on. Read the spec's table of failed nginx drafts before starting.

**Files:**
- Modify: `backend/app/templates/nginx/server.conf.j2`, `backend/app/services/nginx/renderer.py`
- Test: `backend/tests/test_maintenance_blocks.py` (create)

**Interfaces:**
- Consumes: `ProxyHostSpec.maintenance_enabled` / `.maintenance_allow` and `MaintenanceSpec` from Task 2.
- Produces: the rendered `geo` block and per-location guard; `maintenance_html` and `maintenance_retry_after_seconds` passed to the template.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_maintenance_blocks.py`:

```python
"""How a host under maintenance renders.

Every assertion here corresponds to a draft that failed against
openresty/openresty:1.25.3.2-alpine-fat. See the spec's table: a
server-level `if` bypasses error_page entirely, and the mapping placed
after the errors include silently loses to the branded 503.
"""

from __future__ import annotations

from app.core.config import settings
from app.services.nginx.renderer import ERRORS_CONF, MAINTENANCE_HTML, render_config
from app.services.nginx.state import (
    BackendSpec,
    DesiredState,
    LocationSpec,
    MaintenanceSpec,
    ProxyHostSpec,
    UpstreamSpec,
)


def _pool(id: int = 1) -> UpstreamSpec:
    return UpstreamSpec(id=id, name=f"pool-{id}", backends=(BackendSpec(host="10.0.0.5", port=80),))


def _render(**over) -> str:
    host = ProxyHostSpec(id=7, domain_names=("app.example.com",), upstream_id=1, **over)
    state = DesiredState(
        proxy_hosts=(host,),
        http_upstreams=(_pool(),),
        maintenance=MaintenanceSpec(mode="megoopm", retry_after_minutes=15),
    )
    return render_config(state)["megoopm-proxy-7.conf"]


def test_a_host_not_under_maintenance_is_unchanged() -> None:
    conf = _render()
    assert "mgm_maint" not in conf
    assert MAINTENANCE_HTML not in conf


def test_the_bypass_list_becomes_a_geo_block() -> None:
    conf = _render(maintenance_enabled=True, maintenance_allow=("203.0.113.5", "10.0.0.0/8"))

    # Named per host id: two hosts under maintenance must not collide.
    assert "geo $mgm_maint_7 {" in conf
    assert "default 1;" in conf
    assert "203.0.113.5 0;" in conf
    assert "10.0.0.0/8 0;" in conf


def test_every_location_is_guarded() -> None:
    conf = _render(
        maintenance_enabled=True,
        caching_enabled=True,
        locations=(LocationSpec(path="/api/", upstream_id=1),),
    )

    # The root route, the extra location and the asset-cache location. A
    # server-level guard would be tidier and does not work: a return from the
    # rewrite phase bypasses error_page and nginx serves its own body.
    assert conf.count("if ($mgm_maint_7) { return 503; }") == 3


def test_the_acme_challenge_is_never_guarded() -> None:
    """A host left under maintenance must still renew its certificate.

    Maintenance lasts hours or days and renewals are time-sensitive, so
    guarding this turns planned downtime into an expired certificate days
    later, far from the change that caused it.
    """
    conf = _render(maintenance_enabled=True)

    acme = conf[conf.index("/.well-known/acme-challenge/") :]
    acme = acme[: acme.index("}")]
    assert "mgm_maint" not in acme


def test_the_maintenance_mapping_comes_before_the_errors_include() -> None:
    """The ordering the whole feature depends on.

    At one configuration level the *first* error_page for a status wins.
    Reversed, the branded 503 is served and maintenance silently does
    nothing — a config that starts cleanly and is wrong.
    """
    conf = _render(maintenance_enabled=True)

    assert conf.index(f"error_page 503 /{MAINTENANCE_HTML};") < conf.index(ERRORS_CONF)


def test_the_document_is_internal_and_carries_retry_after() -> None:
    conf = _render(maintenance_enabled=True)

    assert f"location = /{MAINTENANCE_HTML} {{" in conf
    assert "internal;" in conf
    assert f"root {settings.nginx_default_dir};" in conf
    # 15 minutes, as configured.
    assert "add_header Retry-After 900 always;" in conf


def test_both_servers_of_a_tls_host_are_guarded() -> None:
    from app.services.nginx.state import CertificateSpec

    cert = CertificateSpec(
        id=1, name="c", fullchain_path="/data/certs/1/fullchain.pem",
        privkey_path="/data/certs/1/privkey.pem",
    )
    conf = _render(maintenance_enabled=True, certificate=cert)

    # The :80 server serves the proxy too when ssl_forced is off, so it needs
    # the guard as much as the :443 one.
    assert conf.count("geo $mgm_maint_7 {") == 1
    assert conf.count(f"error_page 503 /{MAINTENANCE_HTML};") == 2
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_maintenance_blocks.py -p no:cacheprovider -p no:warnings`
Expected: FAIL — nothing emits `mgm_maint`.

- [ ] **Step 3: Add the template macros**

In `backend/app/templates/nginx/server.conf.j2`, add two macros beside the
others (before the `server {` blocks):

```jinja
{%- macro maintenance_guard() %}
{%- if host.maintenance_enabled %}
        # Under maintenance: everyone outside the bypass list gets the page.
        # The guard lives in each location because a server-level `if` runs in
        # the rewrite phase, where a `return` bypasses error_page entirely and
        # nginx serves its own body instead of ours.
        if ($mgm_maint_{{ host.id }}) { return 503; }
{%- endif %}
{%- endmacro -%}
{%- macro maintenance_page() %}
{%- if host.maintenance_enabled %}
    # BEFORE the errors include: at one configuration level the FIRST
    # error_page for a status wins, so reversing these two serves the branded
    # 503 and maintenance silently does nothing.
    error_page 503 /{{ maintenance_html }};
    location = /{{ maintenance_html }} {
        root {{ default_dir }};
        internal;
        add_header Retry-After {{ maintenance_retry_after_seconds }} always;
    }
{%- endif %}
{%- endmacro -%}
```

- [ ] **Step 4: Emit the geo block and call the macros**

At the top of the file, after the `# proxy host id=` comment line and before
the first `server {`:

```jinja
{%- if host.maintenance_enabled %}
# Which addresses skip maintenance for this host. 0 lets the address through.
# `geo` is an http-context directive and this file is included from http{}.
geo $mgm_maint_{{ host.id }} {
    default 1;
{%- for entry in host.maintenance_allow %}
    {{ entry }} 0;
{%- endfor %}
}
{%- endif %}
```

In **both** `server {` blocks, put `{{- maintenance_page() }}` immediately
after the opening brace and **before** the `include {{ default_dir }}/{{ errors_conf }};` line.

Add `{{- maintenance_guard() }}` as the first line inside the location body of:
- `proxy_block` (after `location {{ modifier }}{{ path }} {`),
- `answered_block` (after its `location ^~ {{ loc.path }} {`),
- the asset-cache location in `proxy_location`.

Do **not** add it to `acme_challenge`.

- [ ] **Step 5: Pass the new template variables**

`_render_proxy_host(host: ProxyHostSpec) -> str` (renderer.py:145) takes only
the host today, and its single caller is at line 247. Give it the spec rather
than reaching for a global — the renderer is a pure function of explicit data
and must stay one:

```python
def _render_proxy_host(host: ProxyHostSpec, maintenance: MaintenanceSpec | None) -> str:
```

and in the render call:

```python
        maintenance_html=MAINTENANCE_HTML,
        # Minutes in the UI, seconds in the header: Retry-After is seconds.
        maintenance_retry_after_seconds=(
            (maintenance.retry_after_minutes if maintenance else 60) * 60
        ),
```

At the call site:

```python
        files[f"megoopm-proxy-{host.id}.conf"] = _render_proxy_host(host, state.maintenance)
```

- [ ] **Step 6: Run the tests**

Run: `docker exec megoopm-test python -m pytest tests/test_maintenance_blocks.py -p no:cacheprovider -p no:warnings`
Expected: 7 PASS.

- [ ] **Step 7: Prove it against a real nginx**

The unit tests assert the text; this proves the text does what the spec
claims. Render a config and run it:

```bash
docker exec megoopm-test python -c "
from app.services.nginx.renderer import render_config, render_default_site
from app.services.nginx.state import *
pool = UpstreamSpec(id=1, name='web', backends=(BackendSpec(host='127.0.0.1', port=9), ))
host = ProxyHostSpec(id=7, domain_names=('app.example.com',), upstream_id=1,
                     maintenance_enabled=True, maintenance_allow=('203.0.113.5',))
state = DesiredState(proxy_hosts=(host,), http_upstreams=(pool,),
                     maintenance=MaintenanceSpec(mode='megoopm', retry_after_minutes=15))
print(render_config(state)['megoopm-proxy-7.conf'])
"
```

Read the output and confirm by eye: the `geo` block is above `server`, the
`error_page 503` line precedes the `include`, each proxy location starts with
the guard, and the ACME location does not.

- [ ] **Step 8: Run the full backend suite**

Run: `docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings`
Expected: all pass. `tests/test_nginx_render.py` renders hosts without
maintenance, so nothing there should move.

- [ ] **Step 9: Commit**

```bash
git add backend/app/templates backend/app/services/nginx backend/tests/test_maintenance_blocks.py
git commit -m "feat(maintenance): the server block that serves the page

Three drafts failed against openresty 1.25.3.2 before this one, and each
looked obviously correct. A server-level \`if\` covers every location but
bypasses error_page, so nginx serves its own body; a rewrite to an
internal location fails the same way; and the maintenance mapping placed
after the errors include silently loses to the branded 503, because at
one level the first error_page for a status wins.

What works: the mapping before the include, and the guard inside each
location. The ACME challenge is exempt — a host left under maintenance
must still renew its certificate.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The API

**Files:**
- Modify: `backend/app/schemas/proxy_host.py`, `backend/app/schemas/instance_settings.py`, `backend/app/api/routes/settings.py`, `backend/app/services/nginx/loader.py`, `backend/openapi.json`
- Test: `backend/tests/test_maintenance_api.py` (create)

**Interfaces:**
- Consumes: everything from Tasks 1–3.
- Produces: `PATCH /api/v1/settings/maintenance` taking `MaintenanceUpdate`; `ProxyHostBase.maintenance_enabled` / `.maintenance_allow`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_maintenance_api.py`:

```python
"""The maintenance settings route, and the per-host fields."""

from __future__ import annotations

import pytest
from app.schemas.proxy_host import ProxyHostCreate
from httpx import AsyncClient
from pydantic import ValidationError

URL = "/api/v1/settings/maintenance"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_the_allow_list_takes_addresses_and_ranges() -> None:
    host = ProxyHostCreate(
        domain_names=["a.example.com"],
        upstream_id=1,
        maintenance_enabled=True,
        maintenance_allow=["203.0.113.5", "10.0.0.0/8", "2001:db8::/32"],
    )
    assert host.maintenance_allow == ["203.0.113.5", "10.0.0.0/8", "2001:db8::/32"]


@pytest.mark.parametrize("bad", ["not-an-ip", "999.1.1.1", "10.0.0.0/99", ""])
def test_an_entry_that_is_not_an_address_is_rejected(bad: str) -> None:
    # nginx would refuse to load a geo block containing this, which takes the
    # whole edge down — the API has to catch it first.
    with pytest.raises(ValidationError, match="address"):
        ProxyHostCreate(
            domain_names=["a.example.com"],
            upstream_id=1,
            maintenance_enabled=True,
            maintenance_allow=[bad],
        )


async def test_the_default_is_the_megoopm_page(db_client: AsyncClient, admin_token: str) -> None:
    body = (await db_client.get("/api/v1/settings", headers=_auth(admin_token))).json()
    assert body["maintenance_mode"] == "megoopm"
    assert body["maintenance_page_id"] is None
    assert body["maintenance_retry_after_minutes"] == 60


async def test_choosing_a_custom_page(db_client: AsyncClient, admin_token: str) -> None:
    created = await db_client.post(
        "/api/v1/custom-pages",
        headers=_auth(admin_token),
        json={"name": "Back soon", "description": "", "html": "<h1>brb</h1>"},
    )
    page_id = created.json()["id"]

    resp = await db_client.patch(
        URL,
        headers=_auth(admin_token),
        json={"mode": "custom_page", "page_id": page_id, "retry_after_minutes": 30},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["maintenance_page_id"] == page_id
    assert resp.json()["maintenance_retry_after_minutes"] == 30


async def test_custom_page_without_a_page_is_422(db_client: AsyncClient, admin_token: str) -> None:
    resp = await db_client.patch(
        URL, headers=_auth(admin_token), json={"mode": "custom_page", "page_id": None}
    )
    assert resp.status_code == 422
    assert "page" in resp.text.lower()


async def test_the_megoopm_mode_takes_no_page(db_client: AsyncClient, admin_token: str) -> None:
    resp = await db_client.patch(
        URL, headers=_auth(admin_token), json={"mode": "megoopm", "page_id": 1}
    )
    assert resp.status_code == 422


async def test_a_missing_page_is_422(db_client: AsyncClient, admin_token: str) -> None:
    resp = await db_client.patch(
        URL, headers=_auth(admin_token), json={"mode": "custom_page", "page_id": 999999}
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("minutes", [0, -5, 100000])
async def test_an_unusable_retry_after_is_rejected(
    db_client: AsyncClient, admin_token: str, minutes: int
) -> None:
    # Retry-After is a promise to a crawler; zero or negative is meaningless.
    resp = await db_client.patch(
        URL, headers=_auth(admin_token), json={"mode": "megoopm", "retry_after_minutes": minutes}
    )
    assert resp.status_code == 422


async def test_the_route_is_admin_only(db_client: AsyncClient, member_token: str) -> None:
    resp = await db_client.patch(URL, headers=_auth(member_token), json={"mode": "megoopm"})
    assert resp.status_code == 403
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_maintenance_api.py -p no:cacheprovider -p no:warnings`
Expected: FAIL — the fields and the route do not exist.

- [ ] **Step 3: Add the proxy-host fields**

In `backend/app/schemas/proxy_host.py`, on `ProxyHostBase`:

```python
    maintenance_enabled: bool = Field(
        default=False, description="Serve the maintenance page instead of proxying"
    )
    maintenance_allow: list[str] = Field(
        default_factory=list,
        description="IPs and CIDRs that reach the real site while under maintenance",
    )

    @field_validator("maintenance_allow")
    @classmethod
    def _validate_allow(cls, value: list[str]) -> list[str]:
        """Every entry must be an address or a range.

        nginx refuses to load a geo block containing anything else, and that
        failure takes the whole edge down — so it cannot reach the file.
        """
        for entry in value:
            try:
                ipaddress.ip_network(entry.strip(), strict=False)
            except ValueError as exc:
                raise ValueError(f"{entry!r} is not an IP address or CIDR range.") from exc
        return [entry.strip() for entry in value]
```

Add `import ipaddress` at the top, and add the two fields to
`ProxyHostUpdate` as `bool | None` / `list[str] | None` with the same
validator.

- [ ] **Step 4: Add the settings schema and route**

In `backend/app/schemas/instance_settings.py`, add the read fields to
`InstanceSettingsRead` (`maintenance_mode`, `maintenance_page_id`,
`maintenance_retry_after_minutes`) and a payload:

```python
class MaintenanceUpdate(BaseModel):
    """The maintenance page, chosen once for every host that uses it."""

    mode: MaintenancePageMode
    page_id: int | None = None
    retry_after_minutes: int = Field(default=60, ge=1, le=10080)

    @model_validator(mode="after")
    def _coherent(self) -> MaintenanceUpdate:
        if self.mode is MaintenancePageMode.custom_page and self.page_id is None:
            raise ValueError("Choose a page, or use the MegooPM maintenance page.")
        if self.mode is MaintenancePageMode.megoopm and self.page_id is not None:
            raise ValueError("The MegooPM maintenance page takes no page of its own.")
        return self
```

In `backend/app/api/routes/settings.py`, add beside the ban-page route:

```python
@router.patch("/maintenance", response_model=InstanceSettingsRead)
async def update_maintenance(
    body: MaintenanceUpdate, admin: AdminUser, db: SessionDep, response: Response
) -> InstanceSettingsRead:
    """The page every host under maintenance serves."""
    if body.page_id is not None and await db.get(CustomPage, body.page_id) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No custom page with id {body.page_id}.",
        )
    row = await settings_service.update_maintenance(
        db,
        mode=body.mode,
        page_id=body.page_id,
        retry_after_minutes=body.retry_after_minutes,
    )
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.update,
        object_type="maintenance_page",
        object_id=None,
        meta={"mode": body.mode.value},
    )
    return InstanceSettingsRead.model_validate(row)
```

Add the matching `update_maintenance` to
`backend/app/services/instance_settings.py`, following `update_ban_page`.

- [ ] **Step 5: Wire the loader**

In `backend/app/services/nginx/loader.py`, build the spec beside the ban page:

```python
async def _load_maintenance(session: AsyncSession, row: InstanceSettings) -> MaintenanceSpec:
    """The chosen page, with a custom document dereferenced.

    A page that has gone missing yields an empty ``html``, which the renderer
    reads as "use the shipped page" — an empty maintenance page would be worse
    than a generic one.
    """
    html = ""
    if row.maintenance_mode is MaintenancePageMode.custom_page and row.maintenance_page_id:
        page = await session.get(CustomPage, row.maintenance_page_id)
        html = page.html if page is not None else ""
    return MaintenanceSpec(
        mode=str(row.maintenance_mode),
        html=html,
        retry_after_minutes=row.maintenance_retry_after_minutes,
    )
```

Pass `maintenance=` into `DesiredState`, and copy `maintenance_enabled` and
`tuple(host.maintenance_allow)` into each `ProxyHostSpec` where the other host
flags are copied.

- [ ] **Step 6: Regenerate the API contract**

```bash
docker exec megoopm-test python -m scripts.export_openapi
```

- [ ] **Step 7: Declare the new route's role**

In `backend/tests/test_route_authorization.py`, add to `ROUTE_ROLES`:

```python
    ("PATCH", "/api/v1/settings/maintenance"): "admin",
```

- [ ] **Step 8: Run the tests, then the full suite**

```bash
docker exec megoopm-test python -m pytest tests/test_maintenance_api.py tests/test_route_authorization.py -p no:cacheprovider -p no:warnings
docker exec megoopm-test ruff check app tests
docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings
```
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add backend
git commit -m "feat(maintenance): the API for the switch and the page

The allow-list is validated as addresses and ranges before it can reach
the file: nginx refuses to load a geo block containing anything else, and
that failure takes the whole edge down.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The settings card

**Files:**
- Create: `frontend/src/components/settings/maintenance-card.tsx`, `frontend/src/components/settings/maintenance-card.test.tsx`
- Modify: `frontend/src/lib/api/resources/settings.ts`, `frontend/src/lib/api/index.ts`, `frontend/src/components/settings/settings-view.tsx`, `frontend/src/components/settings/settings-view.test.tsx`

**Interfaces:**
- Consumes: `PATCH /settings/maintenance` from Task 4.
- Produces: `instanceSettings.updateMaintenance(body)`; `MaintenanceCard({ settings, pages, onSaved })`.

- [ ] **Step 1: Regenerate types and add the call**

```bash
cd frontend && npm run gen:api
```

In `src/lib/api/resources/settings.ts`:

```ts
export type MaintenanceUpdate = Schemas["MaintenanceUpdate"];
export type MaintenancePageMode = Schemas["MaintenancePageMode"];
```
```ts
  /** The page every host under maintenance serves; nginx is rewritten. */
  updateMaintenance: (body: MaintenanceUpdate) =>
    api.patch<InstanceSettings>(`${BASE}/maintenance`, body),
```

Re-export both types from `src/lib/api/index.ts`.

- [ ] **Step 2: Write the failing test**

Create `frontend/src/components/settings/maintenance-card.test.tsx`:

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { instanceSettings, type InstanceSettings } from "@/lib/api";
import { MaintenanceCard } from "@/components/settings/maintenance-card";

const PAGES = [{ id: 4, name: "Back soon" }] as never;

function makeSettings(over: Partial<InstanceSettings> = {}): InstanceSettings {
  return {
    maintenance_mode: "megoopm",
    maintenance_page_id: null,
    maintenance_retry_after_minutes: 60,
    ...over,
  } as InstanceSettings;
}

beforeEach(() => {
  vi.spyOn(toast, "success").mockImplementation(() => "" as never);
  vi.spyOn(toast, "error").mockImplementation(() => "" as never);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("MaintenanceCard", () => {
  it("shows a page picker only for the custom mode", async () => {
    const user = userEvent.setup();
    render(<MaintenanceCard settings={makeSettings()} pages={PAGES} onSaved={vi.fn()} />);

    expect(screen.queryByRole("combobox", { name: /page to serve/i })).not.toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: /custom page/i }));
    expect(screen.getByRole("combobox", { name: /page to serve/i })).toBeInTheDocument();
  });

  it("saves the mode, the page and the retry", async () => {
    const user = userEvent.setup();
    const update = vi
      .spyOn(instanceSettings, "updateMaintenance")
      .mockResolvedValue(makeSettings({ maintenance_mode: "custom_page" }));
    render(<MaintenanceCard settings={makeSettings()} pages={PAGES} onSaved={vi.fn()} />);

    await user.click(screen.getByRole("radio", { name: /custom page/i }));
    await user.click(screen.getByRole("combobox", { name: /page to serve/i }));
    await user.click(await screen.findByRole("option", { name: "Back soon" }));
    await user.click(screen.getByRole("button", { name: /save maintenance page/i }));

    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update.mock.calls[0][0]).toMatchObject({ mode: "custom_page", page_id: 4 });
  });

  it("keeps Save disabled until something changes", () => {
    render(<MaintenanceCard settings={makeSettings()} pages={PAGES} onSaved={vi.fn()} />);
    expect(screen.getByRole("button", { name: /save maintenance page/i })).toBeDisabled();
  });

  it("will not save a custom mode with no page chosen", async () => {
    // The API answers 422; offering the button teaches the operator nothing.
    const user = userEvent.setup();
    render(<MaintenanceCard settings={makeSettings()} pages={PAGES} onSaved={vi.fn()} />);

    await user.click(screen.getByRole("radio", { name: /custom page/i }));

    expect(screen.getByRole("button", { name: /save maintenance page/i })).toBeDisabled();
  });

  it("explains what Retry-After is for", () => {
    render(<MaintenanceCard settings={makeSettings()} pages={PAGES} onSaved={vi.fn()} />);
    expect(screen.getByLabelText(/retry after/i)).toHaveValue(60);
  });
});
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/components/settings/maintenance-card.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 4: Write the card**

Create `frontend/src/components/settings/maintenance-card.tsx`, copying the
structure of `ban-page-card.tsx` — a `RadioGroup` over the two modes with
hints, a `PageSelect`-style picker shown only for `custom_page`, an `Input
type="number"` labelled "Retry after (minutes)", and a Save button disabled
unless something differs and the form is coherent. Label the modes:

```tsx
const LABELS: Record<MaintenancePageMode, string> = {
  megoopm: "MegooPM page",
  custom_page: "Custom page",
};

const HINTS: Record<MaintenancePageMode, string> = {
  megoopm: "A branded page saying the site will be back shortly.",
  custom_page: "One of your custom pages.",
};
```

The card's description must state the consequence:

> What a visitor sees on a host you have switched to maintenance. The page is
> served with a 503 and a Retry-After header, so search engines keep your
> rankings and come back rather than de-indexing the site.

- [ ] **Step 5: Run it to verify it passes**

Run: `cd frontend && npx vitest run src/components/settings/maintenance-card.test.tsx`
Expected: 5 PASS.

- [ ] **Step 6: Mount it**

In `settings-view.tsx`, render it after the ban-page card:

```tsx
      {row ? <MaintenanceCard settings={row} pages={pages} onSaved={setRow} /> : null}
```

Check whether `settings-view.test.tsx` needs anything: it mocks
`instanceSettings.get`, and this card takes its data as props, so it should
not need a new mock. Run that file to confirm.

- [ ] **Step 7: Run the frontend suite, typecheck, lint**

```bash
cd frontend && npx vitest run && npx tsc --noEmit && npm run lint
```
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add frontend/src
git commit -m "feat(settings): choose the maintenance page

Mirrors the Ban page card: one choice, instance-wide, plus the
Retry-After the header carries.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: The host switch and the badge

**Files:**
- Modify: `frontend/src/components/proxy-hosts/lib.ts`, `frontend/src/components/proxy-hosts/proxy-host-dialog.tsx`, `frontend/src/components/proxy-hosts/proxy-hosts-view.tsx`
- Test: `frontend/src/components/proxy-hosts/lib.test.ts`, `frontend/src/components/proxy-hosts/proxy-host-dialog.test.tsx`, `frontend/src/components/proxy-hosts/proxy-hosts-view.test.tsx`

**Interfaces:**
- Consumes: `maintenance_enabled` / `maintenance_allow` from Task 4.
- Produces: `ProxyHostFormState.maintenanceEnabled: boolean`, `.maintenanceAllow: string[]`.

- [ ] **Step 1: Write the failing form tests**

Add to `frontend/src/components/proxy-hosts/lib.test.ts`:

```ts
describe("maintenance", () => {
  it("round-trips the switch and the allow list", () => {
    const state = stateFromHost(
      makeHost({ maintenance_enabled: true, maintenance_allow: ["203.0.113.5"] }) as never,
    );
    expect(state.maintenanceEnabled).toBe(true);
    expect(state.maintenanceAllow).toEqual(["203.0.113.5"]);

    const payload = buildPayload(state, null);
    expect(payload.maintenance_enabled).toBe(true);
    expect(payload.maintenance_allow).toEqual(["203.0.113.5"]);
  });

  it("sends an empty allow list rather than null", () => {
    // The column is NOT NULL; null would be a 422 on every save.
    const payload = buildPayload(
      { ...stateFromHost(makeHost()), maintenanceEnabled: true, maintenanceAllow: [] },
      null,
    );
    expect(payload.maintenance_allow).toEqual([]);
  });

  it("rejects an allow-list entry that is not an address", () => {
    const error = validateForm({
      ...stateFromHost(makeHost()),
      maintenanceEnabled: true,
      maintenanceAllow: ["not-an-ip"],
    });
    expect(error?.message).toMatch(/address/i);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/components/proxy-hosts/lib.test.ts`
Expected: FAIL — the fields do not exist.

- [ ] **Step 3: Extend the form model**

In `lib.ts`: add `maintenanceEnabled: boolean` and `maintenanceAllow: string[]`
to `ProxyHostFormState`; read them in `stateFromHost`; send
`maintenance_enabled` and `maintenance_allow` in `buildPayload`; and validate
in `validateForm`:

```ts
  for (const entry of form.maintenanceAllow) {
    if (!isIpOrCidr(entry))
      return { message: `"${entry}" is not an IP address or CIDR range.`, tab: "advanced" };
  }
```

Write `isIpOrCidr` in `lib.ts` with a comment noting the API validates the
same thing — this is the courtesy check, not the enforcement point.

- [ ] **Step 4: Run it to verify it passes**

Run: `cd frontend && npx vitest run src/components/proxy-hosts/lib.test.ts`
Expected: PASS.

- [ ] **Step 5: Write the failing dialog and badge tests**

Add to `proxy-host-dialog.test.tsx`:

```tsx
  it("shows the allow list only when maintenance is on", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("tab", { name: "Advanced" }));

    expect(screen.queryByLabelText(/allowed while under maintenance/i)).not.toBeInTheDocument();
    await user.click(screen.getByLabelText(/under maintenance/i));
    expect(screen.getByLabelText(/allowed while under maintenance/i)).toBeInTheDocument();
  });
```

Add to `proxy-hosts-view.test.tsx`:

```tsx
  it("badges a host that is under maintenance", async () => {
    // The only other signal is inside a dialog, and a host left in
    // maintenance is easy to forget.
    vi.mocked(proxyHosts.list).mockResolvedValue([makeHost({ maintenance_enabled: true })]);
    render(<ProxyHostsView />);
    expect(await screen.findByText(/maintenance/i)).toBeInTheDocument();
  });
```

- [ ] **Step 6: Run them to verify they fail**

Run: `cd frontend && npx vitest run src/components/proxy-hosts`
Expected: the two new tests FAIL.

- [ ] **Step 7: Add the controls**

In the dialog's Advanced tab:

```tsx
        <div className="space-y-2 rounded-xl border p-3">
          <label className="flex items-center justify-between gap-3 text-sm">
            <span>Under maintenance</span>
            <Switch
              checked={form.maintenanceEnabled}
              onCheckedChange={(v) => patch({ maintenanceEnabled: Boolean(v) })}
              aria-label="Under maintenance"
              disabled={saving}
            />
          </label>
          <p className="text-muted-foreground text-xs">
            Every visitor outside the list below is served the maintenance page with a 503. The
            addresses you list still reach the site, so you can check a deploy before switching
            this off.
          </p>
          {form.maintenanceEnabled ? (
            <DomainTagsInput
              label="Allowed while under maintenance"
              values={form.maintenanceAllow}
              onChange={(values) => patch({ maintenanceAllow: values })}
              placeholder="203.0.113.5 or 10.0.0.0/8"
              disabled={saving}
            />
          ) : null}
        </div>
```

Check `DomainTagsInput`'s real prop names in
`frontend/src/components/domains/domain-tags-input.tsx` and match them; if it
is too domain-specific to reuse, a plain comma-separated `Input` is acceptable
— the API is the enforcement point either way.

In `proxy-hosts-view.tsx`, in the domain cell, add beside the existing badges:

```tsx
                      {host.maintenance_enabled ? (
                        <Badge variant="outline" className="text-warning border-warning/40">
                          Maintenance
                        </Badge>
                      ) : null}
```

- [ ] **Step 8: Run the frontend suite, typecheck, lint**

```bash
cd frontend && npx vitest run && npx tsc --noEmit && npm run lint
```
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add frontend/src
git commit -m "feat(proxy-hosts): switch a host into maintenance

A switch and an allow-list in the Advanced tab, and a badge on the list
so a host left in maintenance is visible without opening anything.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Documentation, both suites, teardown

**Files:**
- Modify: `docs/nginx-engine.md`

- [ ] **Step 1: Document it**

In the generated-files section, beside the branded error pages, add
`megoopm-maintenance.html` and the server-block shape. State plainly:

- the `geo` block and the per-location guard, and **why a server-level guard
  does not work** — a `return` from the rewrite phase bypasses `error_page`;
- that the maintenance `error_page` is emitted **before** the errors include,
  because at one level the first `error_page` for a status wins;
- that the ACME challenge location is exempt so certificates still renew;
- that the response is 503 with `Retry-After`;
- how to verify on a live stack:

```bash
curl -sI https://<a-host-under-maintenance>/ | head -1     # 503
curl -sI https://<a-host-under-maintenance>/ | grep -i retry-after
docker compose exec nginx cat /data/nginx/conf.d/megoopm-proxy-<id>.conf | head -20
```

- [ ] **Step 2: Run both full suites**

```bash
docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings
docker exec megoopm-test ruff check app tests alembic
cd frontend && npx vitest run && npx tsc --noEmit && npm run lint
```
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add docs
git commit -m "docs(nginx): maintenance mode, and the two orderings it depends on

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 4: Tear down**

```bash
export MSYS_NO_PATHCONV=1
docker rm -f megoopm-test megoopm-testdb && docker network rm megoopm-testnet
```

---

## Manual verification

- [ ] Rebuild, migrate, and confirm Settings shows the Maintenance page card set to "MegooPM page" with Retry after 60.
- [ ] Switch a proxy host to maintenance with your own IP in the allow-list. From another network (or a phone off wifi) the branded maintenance page appears with the logo; from your own address the real site loads.
- [ ] `curl -sI` that host: `503` and `Retry-After: 3600`.
- [ ] Confirm the branded 404 still works on that host — the maintenance mapping must not have displaced the other error pages.
- [ ] Request `/.well-known/acme-challenge/test` on the host under maintenance: it must not return the maintenance page.
- [ ] Bind maintenance to a Custom Page in Settings, reload, and confirm that document is served instead.
- [ ] Switch maintenance off and confirm `megoopm-maintenance.html` disappears from `/data/nginx/default/`.
- [ ] Confirm the proxy-hosts table badges the host while it is under maintenance.
