# Proxy Host Tabs & Per-Path Locations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the proxy host dialog into Forwarding / Certificate / Advanced tabs, wire the certificate picker, and let a host forward extra URL paths to other upstream pools (`proxy_host_locations`), rendered as `location ^~ <path>` blocks.

**Architecture:** A new child table `proxy_host_locations` (CASCADE from the host, RESTRICT to the pool) rides along the existing `ProxyHost` row; the root `/` route stays on `proxy_hosts.upstream_id`/`forward_scheme`. The API gains a nested `locations` list (replace-in-full on update), the nginx loader/renderer emit one extra prefix location per row sharing the host-wide toggles, and the frontend dialog is rebuilt around controlled tabs with a `LocationsEditor` and pure helpers in `lib.ts`.

**Tech Stack:** FastAPI + SQLAlchemy 2 (async) + Alembic + Jinja2 + pytest (backend); Next.js 16 / React 19 / base-ui + shadcn / vitest + testing-library (frontend).

**Spec:** `docs/superpowers/specs/2026-08-27-proxy-host-tabs-locations-design.md`

## Global Constraints

- Backend line length 100 (`ruff check .` and `ruff format --check <changed files>` must pass; the repo has pre-existing unformatted files — only check the files you touch).
- Backend tests **cannot run on this Windows host** (`fcntl` import). Run them in a throwaway Linux container from the built image, source bind-mounted, joined to the compose network so the Postgres-backed API tests run instead of skipping:
  ```bash
  MSYS_NO_PATHCONV=1 docker run -d --name megoopm-test --network megoopm_default \
    -e DATABASE_URL="$(docker exec megoopm-backend-1 printenv DATABASE_URL)" \
    -v "C:/Projects/MegooPM/backend:/app" -w /app --entrypoint sleep megoopm-backend infinity
  docker exec megoopm-test pip install --user -q pytest pytest-asyncio aiosqlite ruff
  docker exec megoopm-test python -m pytest -q -p no:warnings            # full suite
  docker exec megoopm-test python -m pytest -q -p no:warnings tests/test_x.py -k name
  docker rm -f megoopm-test                                              # when done
  ```
  The API tests roll back everything they write, so pointing them at the dev database is safe. If `megoopm-backend-1` is not running, start the stack first (`docker compose up -d`).
- After any backend change is finished, redeploy the dev stack: `docker compose build backend worker beat && docker compose up -d backend worker beat` (run from the repo root). The backend entrypoint applies Alembic migrations on start.
- `backend/openapi.json` is a committed contract (`tests/test_openapi.py` fails on drift). Regenerate with `docker exec megoopm-test python -m scripts.export_openapi`, then in `frontend/` run `npm run gen:api` to refresh `src/lib/api/generated/schema.ts`.
- Frontend gates (run in `frontend/`): `npm run lint`, `npm run typecheck`, `npm test`.
- Commit messages: conventional prefix (`feat(...)`, `fix(...)`, `docs(...)`, `test(...)`), end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Path rules (verbatim from the spec): starts with `/`; is not exactly `/`; no whitespace and none of `{`, `}`, `;`, `"`; ≤ 255 characters; no duplicates within one payload; `/api` and `/api/` are distinct.
- Locations render as `location ^~ <path> { … }`; host-wide extras (`proxy_http_version 1.1`, `X-Forwarded-*`, websocket headers, `Authorization` stripping) apply to every location.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/app/models/proxy_host.py` | `ProxyHostLocation` model + `ProxyHost.locations` relationship |
| `backend/app/models/__init__.py` | export `ProxyHostLocation` so Alembic autogenerate sees it |
| `backend/alembic/versions/0009_proxy_host_locations.py` | create table, FKs, indexes, unique constraint |
| `backend/app/schemas/proxy_host.py` | `ProxyHostLocationIn` / `ProxyHostLocationRead`, path validator, `locations` on Base/Update/Read |
| `backend/app/services/proxy_host.py` | eager-load locations; validate location pools; replace-in-full on update |
| `backend/app/services/nginx/state.py` | `LocationSpec`, `ProxyHostSpec.locations` |
| `backend/app/services/nginx/loader.py` | load locations + their pools; referenced-upstreams set |
| `backend/app/services/nginx/renderer.py` | pass per-location pool names to the template |
| `backend/app/templates/nginx/server.conf.j2` | parameterised `proxy_location` macro, `^~` extra locations |
| `backend/tests/test_proxy_host_schemas.py` (new) | path rules |
| `backend/tests/test_proxy_hosts_api.py` | locations CRUD, 404/409, preview rendering |
| `backend/tests/test_nginx_render.py` | location blocks |
| `frontend/src/components/proxy-hosts/lib.ts` | form state, validation, payload mapping (React-free) |
| `frontend/src/components/proxy-hosts/locations-editor.tsx` (new) | root row + extra rows table |
| `frontend/src/components/proxy-hosts/proxy-host-dialog.tsx` | tabs shell, certificate tab, advanced tab, submit |
| `frontend/src/components/proxy-hosts/proxy-hosts-view.tsx` | load certificates, pass `certs` |
| `frontend/src/components/proxy-hosts/lib.test.ts`, `proxy-host-dialog.test.tsx` (new) | tests |
| `docs/data-model.md`, `docs/nginx-engine.md` | docs |

---

### Task 1: `ProxyHostLocation` model and migration

**Files:**
- Modify: `backend/app/models/proxy_host.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/0009_proxy_host_locations.py`
- Test: `backend/tests/test_proxy_host_model.py` (new)

**Interfaces:**
- Produces: `app.models.proxy_host.ProxyHostLocation` with columns `id`, `proxy_host_id`, `path`, `upstream_id`, `forward_scheme: HttpScheme`, `created_at`, `updated_at`; relationships `ProxyHostLocation.proxy_host`, `ProxyHostLocation.upstream`, `ProxyHost.locations: list[ProxyHostLocation]` (ordered by `path`, `cascade="all, delete-orphan"`).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_proxy_host_model.py`:

```python
"""Structural checks for the proxy_host_locations mapping (no database)."""

from __future__ import annotations

from app.models.proxy_host import ProxyHost, ProxyHostLocation


def test_location_table_shape() -> None:
    table = ProxyHostLocation.__table__
    assert table.name == "proxy_host_locations"
    assert {c.name for c in table.columns} >= {
        "id", "proxy_host_id", "path", "upstream_id", "forward_scheme", "created_at", "updated_at"
    }
    fks = {fk.column.table.name: fk.ondelete for fk in table.foreign_keys}
    assert fks == {"proxy_hosts": "CASCADE", "upstreams": "RESTRICT"}
    unique = [c for c in table.constraints if c.__class__.__name__ == "UniqueConstraint"]
    assert [tuple(col.name for col in u.columns) for u in unique] == [("proxy_host_id", "path")]


def test_host_locations_relationship_cascades_orphans() -> None:
    rel = ProxyHost.__mapper__.relationships["locations"]
    assert rel.cascade.delete_orphan
    assert rel.mapper.class_ is ProxyHostLocation
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec megoopm-test python -m pytest -q -p no:warnings tests/test_proxy_host_model.py`
Expected: FAIL with `ImportError: cannot import name 'ProxyHostLocation'`

- [ ] **Step 3: Add the model**

In `backend/app/models/proxy_host.py`, extend the imports and add the class after `ProxyHost`:

```python
from sqlalchemy import Boolean, Enum, ForeignKey, String, Text, UniqueConstraint
```

Inside `ProxyHost`, after the `certificate` relationship:

```python
    # Extra path-prefixed routes (``location ^~ /path``) to other pools. The
    # root ``/`` route is ``upstream_id``/``forward_scheme`` above.
    locations: Mapped[list[ProxyHostLocation]] = relationship(
        back_populates="proxy_host",
        cascade="all, delete-orphan",
        order_by="ProxyHostLocation.path",
    )
```

New class:

```python
class ProxyHostLocation(IdMixin, TimestampMixin, Base):
    """One ``location <path>`` block of a proxy host forwarding to a pool."""

    __tablename__ = "proxy_host_locations"
    __table_args__ = (
        UniqueConstraint(
            "proxy_host_id", "path", name="uq_proxy_host_locations_proxy_host_id_path"
        ),
    )

    proxy_host_id: Mapped[int] = mapped_column(
        ForeignKey("proxy_hosts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    # RESTRICT, like ``proxy_hosts.upstream_id``: a pool in use cannot be deleted.
    upstream_id: Mapped[int] = mapped_column(
        ForeignKey("upstreams.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    forward_scheme: Mapped[HttpScheme] = mapped_column(
        Enum(
            HttpScheme,
            name="http_scheme",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=HttpScheme.http,
        server_default=HttpScheme.http.value,
    )

    proxy_host: Mapped[ProxyHost] = relationship(back_populates="locations")
    upstream: Mapped[Upstream] = relationship()
```

Update `__all__ = ["ProxyHost", "ProxyHostLocation"]`. In `backend/app/models/__init__.py`, add `ProxyHostLocation` next to `ProxyHost` in both the import and `__all__` (same pattern the file already uses for every model).

- [ ] **Step 4: Write the migration**

Create `backend/alembic/versions/0009_proxy_host_locations.py`:

```python
"""Proxy host locations: extra path-prefixed routes to other upstream pools

Adds ``proxy_host_locations``: one row per ``location ^~ <path>`` block a proxy
host forwards to a pool other than its root one. CASCADE from the host,
RESTRICT to the pool (mirrors ``proxy_hosts.upstream_id``). Reuses the
existing ``http_scheme`` enum type.

Purely additive and fully reversible.

Revision ID: 0009_proxy_host_locations
Revises: 0008_dns_provider_credentials
Create Date: 2026-08-27 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009_proxy_host_locations"
down_revision: str | None = "0008_dns_provider_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "proxy_host_locations",
        sa.Column("proxy_host_id", sa.BigInteger(), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("upstream_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "forward_scheme",
            postgresql.ENUM("http", "https", name="http_scheme", create_type=False),
            server_default="http",
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
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
        sa.ForeignKeyConstraint(
            ["proxy_host_id"],
            ["proxy_hosts.id"],
            name=op.f("fk_proxy_host_locations_proxy_host_id_proxy_hosts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["upstream_id"],
            ["upstreams.id"],
            name=op.f("fk_proxy_host_locations_upstream_id_upstreams"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proxy_host_locations")),
        sa.UniqueConstraint(
            "proxy_host_id", "path", name="uq_proxy_host_locations_proxy_host_id_path"
        ),
    )
    op.create_index(
        op.f("ix_proxy_host_locations_proxy_host_id"),
        "proxy_host_locations",
        ["proxy_host_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_proxy_host_locations_upstream_id"),
        "proxy_host_locations",
        ["upstream_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_proxy_host_locations_upstream_id"), table_name="proxy_host_locations")
    op.drop_index(op.f("ix_proxy_host_locations_proxy_host_id"), table_name="proxy_host_locations")
    op.drop_table("proxy_host_locations")
```

- [ ] **Step 5: Run the tests and the migration check**

Run: `docker exec megoopm-test python -m pytest -q -p no:warnings tests/test_proxy_host_model.py`
Expected: PASS (2 tests)

Run: `docker exec megoopm-test sh -c 'alembic upgrade head && alembic check'`
Expected: `INFO ... Running upgrade 0008_dns_provider_credentials -> 0009_proxy_host_locations` then `No new upgrade operations detected.` (this applies the migration to the dev database; the backend container will find it already applied on its next start).

Run: `docker exec megoopm-test sh -c 'python -m ruff check app/models alembic/versions/0009_proxy_host_locations.py tests/test_proxy_host_model.py && python -m ruff format --check app/models/proxy_host.py alembic/versions/0009_proxy_host_locations.py tests/test_proxy_host_model.py'`
Expected: `All checks passed!` / `already formatted`

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/proxy_host.py backend/app/models/__init__.py backend/alembic/versions/0009_proxy_host_locations.py backend/tests/test_proxy_host_model.py
git commit -m "feat(proxy-hosts): proxy_host_locations table and model" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Location schemas and path rules

**Files:**
- Modify: `backend/app/schemas/proxy_host.py`
- Test: `backend/tests/test_proxy_host_schemas.py` (new)

**Interfaces:**
- Produces: `ProxyHostLocationIn(path: str, upstream_id: int, forward_scheme: HttpScheme = http)`, `ProxyHostLocationRead(ProxyHostLocationIn) + id: int`; `ProxyHostBase.locations: list[ProxyHostLocationIn] = []`; `ProxyHostUpdate.locations: list[ProxyHostLocationIn] | None = None`; `ProxyHostRead.locations: list[ProxyHostLocationRead]`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_proxy_host_schemas.py`:

```python
"""Validation rules for proxy host location paths (pure pydantic, no DB)."""

from __future__ import annotations

import pytest
from app.schemas.proxy_host import ProxyHostCreate, ProxyHostLocationIn, ProxyHostUpdate
from pydantic import ValidationError


def _create(**locations_kw) -> ProxyHostCreate:
    return ProxyHostCreate(domain_names=["a.example.com"], upstream_id=1, **locations_kw)


def test_location_defaults_to_http_and_keeps_trailing_slash_distinct() -> None:
    loc = ProxyHostLocationIn(path="/api/", upstream_id=2)
    assert loc.forward_scheme == "http"
    assert loc.path == "/api/"
    assert ProxyHostLocationIn(path="/api", upstream_id=2).path == "/api"


@pytest.mark.parametrize(
    ("path", "fragment"),
    [
        ("api", "start with '/'"),
        ("/", "root"),
        ("/a b", "whitespace"),
        ("/a;b", "whitespace"),
        ('/a"b', "whitespace"),
        ("/a{b}", "whitespace"),
        ("/" + "x" * 255, "255"),
    ],
)
def test_invalid_paths_are_rejected(path: str, fragment: str) -> None:
    with pytest.raises(ValidationError, match=fragment):
        ProxyHostLocationIn(path=path, upstream_id=2)


def test_duplicate_paths_in_one_payload_are_rejected() -> None:
    rows = [{"path": "/api", "upstream_id": 2}, {"path": "/api", "upstream_id": 3}]
    with pytest.raises(ValidationError, match="duplicate location path"):
        _create(locations=rows)
    with pytest.raises(ValidationError, match="duplicate location path"):
        ProxyHostUpdate(locations=rows)


def test_locations_default_empty_and_update_none_means_unchanged() -> None:
    assert _create().locations == []
    assert ProxyHostUpdate().model_dump(exclude_unset=True) == {}
    assert ProxyHostUpdate(locations=[]).locations == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker exec megoopm-test python -m pytest -q -p no:warnings tests/test_proxy_host_schemas.py`
Expected: FAIL with `ImportError: cannot import name 'ProxyHostLocationIn'`

- [ ] **Step 3: Implement the schemas**

In `backend/app/schemas/proxy_host.py`, after `_normalise_domains` add:

```python
_LOCATION_FORBIDDEN = frozenset('{};"')


def _validate_location_path(value: str) -> str:
    """Enforce the spec's path rules; the value is embedded in a ``location`` directive."""
    path = value.strip()
    if not path.startswith("/"):
        raise ValueError("location path must start with '/'")
    if path == "/":
        raise ValueError("'/' is the host's root route; use a sub-path such as /api/")
    if any(ch.isspace() or ch in _LOCATION_FORBIDDEN for ch in path):
        raise ValueError('location path must not contain whitespace or any of { } ; "')
    if len(path) > 255:
        raise ValueError("location path must be at most 255 characters")
    return path


def _unique_location_paths(value: list[ProxyHostLocationIn]) -> list[ProxyHostLocationIn]:
    seen: set[str] = set()
    for loc in value:
        if loc.path in seen:
            raise ValueError(f"duplicate location path: {loc.path!r}")
        seen.add(loc.path)
    return value


class ProxyHostLocationIn(BaseModel):
    """One extra ``location <path>`` route of a proxy host."""

    path: str = Field(description="URL prefix, e.g. /api/ (the root '/' is the host itself)")
    upstream_id: int = Field(description="Pool this prefix forwards to")
    forward_scheme: HttpScheme = Field(
        default=HttpScheme.http, description="Scheme used to reach the pool (http/https)"
    )

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return _validate_location_path(value)


class ProxyHostLocationRead(ProxyHostLocationIn):
    """Stored location (adds the row id)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
```

Add to `ProxyHostBase` (after `advanced_config`):

```python
    locations: list[ProxyHostLocationIn] = Field(
        default_factory=list,
        description="Extra path-prefixed routes to other pools (rendered as location ^~ <path>)",
    )

    @field_validator("locations")
    @classmethod
    def _validate_locations(cls, value: list[ProxyHostLocationIn]) -> list[ProxyHostLocationIn]:
        return _unique_location_paths(value)
```

Add to `ProxyHostUpdate` (after `enabled`):

```python
    locations: list[ProxyHostLocationIn] | None = None

    @field_validator("locations")
    @classmethod
    def _validate_locations(
        cls, value: list[ProxyHostLocationIn] | None
    ) -> list[ProxyHostLocationIn] | None:
        if value is None:
            return None
        return _unique_location_paths(value)
```

Add to `ProxyHostRead` (after `updated_at`):

```python
    locations: list[ProxyHostLocationRead] = Field(default_factory=list)
```

Extend `__all__` with `"ProxyHostLocationIn", "ProxyHostLocationRead"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker exec megoopm-test python -m pytest -q -p no:warnings tests/test_proxy_host_schemas.py`
Expected: PASS (all)

Run: `docker exec megoopm-test sh -c 'python -m ruff check app/schemas/proxy_host.py tests/test_proxy_host_schemas.py && python -m ruff format --check app/schemas/proxy_host.py tests/test_proxy_host_schemas.py'`
Expected: clean

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/proxy_host.py backend/tests/test_proxy_host_schemas.py
git commit -m "feat(proxy-hosts): location schemas and path validation" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Service — persist locations, eager loading, API contract

**Files:**
- Modify: `backend/app/services/proxy_host.py`
- Modify: `backend/tests/test_proxy_hosts_api.py`
- Regenerate: `backend/openapi.json`, `frontend/src/lib/api/generated/schema.ts`

**Interfaces:**
- Consumes: `ProxyHostLocation` (Task 1); `values["locations"]` / `changes["locations"]` as lists of `{"path", "upstream_id", "forward_scheme"}` dicts from `model_dump()` (Task 2).
- Produces: `get_proxy_host` / `list_proxy_hosts` return hosts with `locations` eagerly loaded; `create_proxy_host` / `update_proxy_host` accept and persist `locations`; `InvalidReferenceError` for an unknown location pool (the route already maps it to **422**, same as the root pool).

- [ ] **Step 1: Write the failing API tests**

Append to `backend/tests/test_proxy_hosts_api.py` (before the `# --- RBAC` section):

```python
# --- Locations (per-path routes to other pools) ----------------------------


async def _make_named_pool(client: AsyncClient, auth, name: str, backends: bool = True) -> int:
    resp = await client.post(
        "/api/v1/upstreams",
        headers=auth,
        json={
            "name": name,
            "backends": [{"host": "10.0.0.9", "port": 8080}] if backends else [],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_locations_crud_replace_in_full(client: AsyncClient, auth) -> None:
    root = await _make_pool(client, auth)
    api = await _make_named_pool(client, auth, "api-pool")
    ws = await _make_named_pool(client, auth, "ws-pool")

    created = await client.post(
        "/api/v1/proxy-hosts",
        headers=auth,
        json={
            "domain_names": ["loc.example.com"],
            "upstream_id": root,
            "locations": [{"path": "/api/", "upstream_id": api, "forward_scheme": "https"}],
        },
    )
    assert created.status_code == 201, created.text
    host = created.json()
    assert [(l["path"], l["upstream_id"], l["forward_scheme"]) for l in host["locations"]] == [
        ("/api/", api, "https")
    ]
    host_id = host["id"]

    # Omitted → unchanged.
    patched = await client.patch(
        f"/api/v1/proxy-hosts/{host_id}", headers=auth, json={"block_exploits": True}
    )
    assert patched.status_code == 200, patched.text
    assert [l["path"] for l in patched.json()["locations"]] == ["/api/"]

    # A list → replaced in full (sorted by path on read).
    patched = await client.patch(
        f"/api/v1/proxy-hosts/{host_id}",
        headers=auth,
        json={
            "locations": [
                {"path": "/ws", "upstream_id": ws},
                {"path": "/admin/", "upstream_id": api},
            ]
        },
    )
    assert patched.status_code == 200, patched.text
    assert [(l["path"], l["upstream_id"]) for l in patched.json()["locations"]] == [
        ("/admin/", api),
        ("/ws", ws),
    ]

    listed = await client.get("/api/v1/proxy-hosts", headers=auth)
    row = next(h for h in listed.json() if h["id"] == host_id)
    assert len(row["locations"]) == 2

    # [] → cleared.
    patched = await client.patch(
        f"/api/v1/proxy-hosts/{host_id}", headers=auth, json={"locations": []}
    )
    assert patched.json()["locations"] == []


async def test_location_with_unknown_pool_is_rejected(client: AsyncClient, auth) -> None:
    root = await _make_pool(client, auth)
    resp = await client.post(
        "/api/v1/proxy-hosts",
        headers=auth,
        json={
            "domain_names": ["badloc.example.com"],
            "upstream_id": root,
            "locations": [{"path": "/api/", "upstream_id": 999999}],
        },
    )
    assert resp.status_code == 422, resp.text
    assert "999999" in resp.json()["detail"]


async def test_pool_used_only_by_a_location_cannot_be_deleted(client: AsyncClient, auth) -> None:
    root = await _make_pool(client, auth)
    api = await _make_named_pool(client, auth, "api-only")
    await client.post(
        "/api/v1/proxy-hosts",
        headers=auth,
        json={
            "domain_names": ["restrict.example.com"],
            "upstream_id": root,
            "locations": [{"path": "/api/", "upstream_id": api}],
        },
    )
    resp = await client.delete(f"/api/v1/upstreams/{api}", headers=auth)
    assert resp.status_code == 409, resp.text


async def test_deleting_host_cascades_locations(client: AsyncClient, auth) -> None:
    root = await _make_pool(client, auth)
    api = await _make_named_pool(client, auth, "cascade-pool")
    created = await client.post(
        "/api/v1/proxy-hosts",
        headers=auth,
        json={
            "domain_names": ["cascade.example.com"],
            "upstream_id": root,
            "locations": [{"path": "/api/", "upstream_id": api}],
        },
    )
    host_id = created.json()["id"]
    assert (await client.delete(f"/api/v1/proxy-hosts/{host_id}", headers=auth)).status_code == 204
    # The location row is gone, so the pool is deletable again.
    assert (await client.delete(f"/api/v1/upstreams/{api}", headers=auth)).status_code == 204
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker exec megoopm-test python -m pytest -q -p no:warnings tests/test_proxy_hosts_api.py -k "locations or location or cascades"`
Expected: FAIL — `test_locations_crud_replace_in_full` gets `locations == []` in the create response (or a `MissingGreenlet`/lazy-load error), the others fail similarly. If they SKIP with "No database reachable", fix the container's `DATABASE_URL` (Global Constraints) before continuing.

- [ ] **Step 3: Implement the service changes**

Replace the body of `backend/app/services/proxy_host.py` from `_upstream_exists` down to (but not including) `delete_proxy_host` with:

```python
from sqlalchemy.orm import selectinload

from app.models.proxy_host import ProxyHost, ProxyHostLocation
```

(merge these into the existing import block), then:

```python
async def _missing_upstreams(db: AsyncSession, ids: set[int]) -> set[int]:
    """Subset of ``ids`` that does not exist as a pool."""
    if not ids:
        return set()
    found = set((await db.scalars(select(Upstream.id).where(Upstream.id.in_(ids)))).all())
    return ids - found


async def _upstream_exists(db: AsyncSession, upstream_id: int) -> bool:
    return not await _missing_upstreams(db, {upstream_id})


def _location_rows(locations: list[dict[str, Any]]) -> list[ProxyHostLocation]:
    return [ProxyHostLocation(**loc) for loc in locations]


async def _check_location_pools(db: AsyncSession, locations: list[dict[str, Any]]) -> None:
    missing = await _missing_upstreams(db, {loc["upstream_id"] for loc in locations})
    if missing:
        raise InvalidReferenceError(
            "location upstream(s) do not exist: " + ", ".join(str(i) for i in sorted(missing))
        )


def _with_locations(stmt):
    return stmt.options(selectinload(ProxyHost.locations))


async def get_proxy_host(db: AsyncSession, host_id: int) -> ProxyHost | None:
    """Return the proxy host (with its locations) or ``None``."""
    return await db.scalar(_with_locations(select(ProxyHost).where(ProxyHost.id == host_id)))


async def list_proxy_hosts(db: AsyncSession) -> list[ProxyHost]:
    """Return all proxy hosts (with locations) ordered by id."""
    result = await db.execute(_with_locations(select(ProxyHost)).order_by(ProxyHost.id))
    return list(result.scalars().all())


async def create_proxy_host(db: AsyncSession, values: dict[str, Any]) -> ProxyHost:
    """Create a proxy host.

    Raises :class:`InvalidReferenceError` if the target pool, a location's pool,
    or an optional certificate/access list does not exist.
    """
    if not await _upstream_exists(db, values["upstream_id"]):
        raise InvalidReferenceError(f"upstream {values['upstream_id']} does not exist")
    locations = values.get("locations") or []
    await _check_location_pools(db, locations)

    fields = {k: v for k, v in values.items() if k != "locations"}
    host = ProxyHost(**fields, locations=_location_rows(locations))
    db.add(host)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise InvalidReferenceError(str(exc.orig)) from exc
    refreshed = await get_proxy_host(db, host.id)
    assert refreshed is not None
    return refreshed


async def update_proxy_host(
    db: AsyncSession, host_id: int, changes: dict[str, Any]
) -> ProxyHost:
    """Apply a partial update to a proxy host.

    ``changes["locations"]`` (when present) replaces the location list in full;
    ``delete-orphan`` removes the old rows. Raises
    :class:`ProxyHostNotFoundError` if missing or :class:`InvalidReferenceError`
    if a changed reference is invalid.
    """
    host = await get_proxy_host(db, host_id)
    if host is None:
        raise ProxyHostNotFoundError(str(host_id))

    new_upstream = changes.get("upstream_id")
    if new_upstream is not None and not await _upstream_exists(db, new_upstream):
        raise InvalidReferenceError(f"upstream {new_upstream} does not exist")
    locations = changes.get("locations")
    if locations is not None:
        await _check_location_pools(db, locations)

    for field, value in changes.items():
        if field == "locations":
            continue
        setattr(host, field, value)
    if locations is not None:
        host.locations = _location_rows(locations)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise InvalidReferenceError(str(exc.orig)) from exc
    refreshed = await get_proxy_host(db, host_id)
    assert refreshed is not None
    return refreshed
```

Keep `delete_proxy_host` as is (it calls `get_proxy_host`, which now eager-loads). Update the module docstring's last paragraph to mention location pools are validated the same way.

- [ ] **Step 4: Run the tests**

Run: `docker exec megoopm-test python -m pytest -q -p no:warnings tests/test_proxy_hosts_api.py`
Expected: PASS (all, including the pre-existing ones)

Run: `docker exec megoopm-test sh -c 'python -m ruff check app/services/proxy_host.py tests/test_proxy_hosts_api.py && python -m ruff format --check app/services/proxy_host.py'`
Expected: clean (the test file is on the pre-existing "unformatted" list — only `ruff check` applies to it)

- [ ] **Step 5: Regenerate the contract**

Run: `docker exec megoopm-test python -m scripts.export_openapi`
Run (in `frontend/`): `npm run gen:api`
Run: `docker exec megoopm-test python -m pytest -q -p no:warnings tests/test_openapi.py`
Expected: PASS; `git diff --stat` shows `backend/openapi.json` and `frontend/src/lib/api/generated/schema.ts` changed, with `ProxyHostLocationIn` / `ProxyHostLocationRead` present in both.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/proxy_host.py backend/tests/test_proxy_hosts_api.py backend/openapi.json frontend/src/lib/api/generated/schema.ts
git commit -m "feat(proxy-hosts): persist per-path locations; locations in the API contract" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: nginx rendering — `LocationSpec`, loader, template

**Files:**
- Modify: `backend/app/services/nginx/state.py`
- Modify: `backend/app/services/nginx/loader.py`
- Modify: `backend/app/services/nginx/renderer.py:92-108`
- Modify: `backend/app/templates/nginx/server.conf.j2`
- Modify: `docs/data-model.md`, `docs/nginx-engine.md`
- Test: `backend/tests/test_nginx_render.py`, `backend/tests/test_proxy_hosts_api.py`

**Interfaces:**
- Consumes: `ProxyHost.locations` / `ProxyHostLocation.upstream` (Task 1).
- Produces: `LocationSpec(path: str, upstream_id: int, forward_scheme: str = "http")`; `ProxyHostSpec.locations: tuple[LocationSpec, ...] = ()`; template context `location_pools: dict[int, str]`.

- [ ] **Step 1: Write the failing renderer tests**

Append to `backend/tests/test_nginx_render.py` (add `LocationSpec` to the `state` import):

```python
def test_extra_locations_render_prefix_blocks_per_pool() -> None:
    api_pool = _pool(id=2, name="api-pool")
    host = _host(
        allow_websocket_upgrade=True,
        locations=(LocationSpec(path="/api/", upstream_id=2, forward_scheme="https"),),
    )
    out = render_config(DesiredState(proxy_hosts=(host,), upstreams=(_pool(), api_pool)))
    server = out["megoopm-proxy-1.conf"]
    # Root keeps its plain prefix location; the extra one uses ^~ so it beats the
    # cache-assets regex location for paths under it.
    assert "location / {" in server
    assert "location ^~ /api/ {" in server
    assert "proxy_pass http://megoopm_upstream_1;" in server
    assert "proxy_pass https://megoopm_upstream_2;" in server
    # Host-wide extras apply to every location.
    assert server.count("proxy_set_header Upgrade $http_upgrade;") == 2
    assert server.count("proxy_http_version 1.1;") == 2
    assert "megoopm-upstream-2.conf" in out


def test_extra_locations_appear_in_both_servers_of_a_tls_host() -> None:
    cert = CertificateSpec(
        id=3,
        fullchain_path="/etc/nginx/certs/3/fullchain.pem",
        privkey_path="/etc/nginx/certs/3/privkey.pem",
    )
    host = _host(
        certificate=cert,
        ssl_forced=False,
        locations=(LocationSpec(path="/ws", upstream_id=2),),
    )
    out = render_config(DesiredState(proxy_hosts=(host,), upstreams=(_pool(), _pool(id=2))))
    server = out["megoopm-proxy-1.conf"]
    assert server.count("location ^~ /ws {") == 2  # :80 and :443 servers
    assert server.count("proxy_pass http://megoopm_upstream_2;") == 2


def test_cache_location_is_unchanged_with_extra_locations() -> None:
    host = _host(caching_enabled=True, locations=(LocationSpec(path="/api/", upstream_id=2),))
    out = render_config(DesiredState(proxy_hosts=(host,), upstreams=(_pool(), _pool(id=2))))
    server = out["megoopm-proxy-1.conf"]
    assert server.count("expires 1d;") == 1
    assert "location ^~ /api/ {" in server
```

And the loader/preview test, appended to `backend/tests/test_proxy_hosts_api.py` (before `# --- RBAC`):

```python
async def test_locations_render_in_preview_and_skip_empty_pools(client: AsyncClient, auth) -> None:
    root = await _make_pool(client, auth)
    api = await _make_named_pool(client, auth, "preview-api")
    empty = await _make_named_pool(client, auth, "preview-empty", backends=False)
    await client.post(
        "/api/v1/proxy-hosts",
        headers=auth,
        json={
            "domain_names": ["preview.example.com"],
            "upstream_id": root,
            "locations": [
                {"path": "/api/", "upstream_id": api, "forward_scheme": "https"},
                {"path": "/void/", "upstream_id": empty},
            ],
        },
    )
    preview = await client.get("/api/v1/nginx/preview", headers=auth)
    assert preview.status_code == 200, preview.text
    files = {f["name"]: f["content"] for f in preview.json()["files"]}
    config = "\n".join(files.values())
    assert f"upstream megoopm_upstream_{api} {{" in config
    assert f"proxy_pass https://megoopm_upstream_{api};" in config
    assert "location ^~ /api/ {" in config
    # A location whose pool has no backends is dropped; its pool is not emitted.
    assert "/void/" not in config
    assert f"megoopm-upstream-{empty}.conf" not in files
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker exec megoopm-test python -m pytest -q -p no:warnings tests/test_nginx_render.py -k locations`
Expected: FAIL with `ImportError: cannot import name 'LocationSpec'`

- [ ] **Step 3: Add `LocationSpec` to the state**

In `backend/app/services/nginx/state.py`, before `ProxyHostSpec`:

```python
@dataclass(frozen=True, slots=True)
class LocationSpec:
    """An extra ``location ^~ <path>`` route of a proxy host to another pool."""

    path: str
    upstream_id: int
    forward_scheme: str = "http"
```

Add to `ProxyHostSpec` after `advanced_config`:

```python
    # Extra path-prefixed routes; the root ``/`` is ``upstream_id``/``forward_scheme``.
    locations: tuple[LocationSpec, ...] = ()
```

Add `"LocationSpec"` to `__all__`.

- [ ] **Step 4: Load locations**

In `backend/app/services/nginx/loader.py`:

Import `ProxyHostLocation` from `app.models.proxy_host` and `LocationSpec` from `.state`. Extend the `select(ProxyHost)` options with:

```python
            selectinload(ProxyHost.locations)
            .selectinload(ProxyHostLocation.upstream)
            .selectinload(Upstream.backends),
```

Inside the `for host in hosts:` loop, after the `certificate = ...` assignment and before `host_specs.append(...)`, build the location specs:

```python
        location_specs: list[LocationSpec] = []
        for location in sorted(host.locations, key=lambda loc: loc.path):
            loc_pool = location.upstream
            if loc_pool is None or not loc_pool.enabled:
                continue
            if loc_pool.id not in upstreams:
                upstreams[loc_pool.id] = _upstream_spec(loc_pool)
            if not upstreams[loc_pool.id].backends:
                continue  # empty pool → drop this location, keep the host
            location_specs.append(
                LocationSpec(
                    path=location.path,
                    upstream_id=loc_pool.id,
                    forward_scheme=str(location.forward_scheme),
                )
            )
```

Pass `locations=tuple(location_specs)` into `ProxyHostSpec(...)`. Replace the referenced-set line with:

```python
    referenced = {h.upstream_id for h in host_specs}
    referenced |= {loc.upstream_id for h in host_specs for loc in h.locations}
```

- [ ] **Step 5: Render locations**

In `backend/app/services/nginx/renderer.py`, `_render_proxy_host`, add to the `render(...)` call:

```python
        # Pool name per extra location's upstream id (root pool is ``pool_name``).
        location_pools={loc.upstream_id: pool_name(loc.upstream_id) for loc in host.locations},
```

In `backend/app/templates/nginx/server.conf.j2`, replace the `proxy_location` macro (everything from `{%- macro proxy_location() %}` to its `{%- endmacro -%}`) with:

```jinja
{%- macro proxy_block(path, scheme, pool, modifier="") %}
    location {{ modifier }}{{ path }} {
        proxy_pass {{ scheme }}://{{ pool }};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
{%- if host.allow_websocket_upgrade %}
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
{%- endif %}
{%- if host.access_list and host.access_list.auth_users and not host.access_list.pass_auth %}
        # Access list consumes the basic-auth header; do not forward it upstream.
        proxy_set_header Authorization "";
{%- endif %}
    }
{%- endmacro -%}
{%- macro proxy_location() %}
{{- proxy_block("/", host.forward_scheme, pool_name) }}
{%- for loc in host.locations %}

    # Extra route: ^~ makes the longest prefix win over the cache-assets regex.
{{- proxy_block(loc.path, loc.forward_scheme, location_pools[loc.upstream_id], "^~ ") }}
{%- endfor %}
{%- if host.caching_enabled %}

    location ~* \.(?:jpg|jpeg|gif|png|ico|css|js|woff2?|svg)$ {
        proxy_pass {{ host.forward_scheme }}://{{ pool_name }};
        proxy_set_header Host $host;
        expires 1d;
        add_header Cache-Control "public";
    }
{%- endif %}
{%- endmacro -%}
```

The three call sites (`{{- proxy_location() }}`) stay unchanged.

- [ ] **Step 6: Run the tests**

Run: `docker exec megoopm-test python -m pytest -q -p no:warnings tests/test_nginx_render.py tests/test_proxy_hosts_api.py tests/test_nginx_api.py tests/test_nginx_engine.py`
Expected: PASS (all)

Run: `docker exec megoopm-test sh -c 'python -m ruff check app/services/nginx tests/test_nginx_render.py && python -m ruff format --check app/services/nginx/state.py app/services/nginx/loader.py app/services/nginx/renderer.py'`
Expected: clean

- [ ] **Step 7: Prove nginx accepts the output**

Redeploy and validate the real config with a host that has a location (uses the dev stack; the login credentials are `FIRST_ADMIN_EMAIL` / `FIRST_ADMIN_PASSWORD` from `.env` unless changed):

```bash
cd /c/Projects/MegooPM && docker compose build backend worker beat && docker compose up -d backend worker beat
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"changeme"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
A=$(curl -s -X POST http://localhost:8000/api/v1/upstreams -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"plan-root","backends":[{"host":"10.0.0.1","port":8080}]}' | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
B=$(curl -s -X POST http://localhost:8000/api/v1/upstreams -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"plan-api","backends":[{"host":"10.0.0.2","port":8080}]}' | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
H=$(curl -s -X POST http://localhost:8000/api/v1/proxy-hosts -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"domain_names\":[\"plan.example.com\"],\"upstream_id\":$A,\"locations\":[{\"path\":\"/api/\",\"upstream_id\":$B,\"forward_scheme\":\"https\"}]}" | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
docker exec megoopm-nginx openresty -t
docker exec megoopm-nginx sh -c "grep -A2 'location ^~ /api/' /etc/nginx/conf.d/megoopm-proxy-$H.conf"
# clean up
curl -s -X DELETE http://localhost:8000/api/v1/proxy-hosts/$H -H "Authorization: Bearer $TOKEN"
curl -s -X DELETE http://localhost:8000/api/v1/upstreams/$A -H "Authorization: Bearer $TOKEN"
curl -s -X DELETE http://localhost:8000/api/v1/upstreams/$B -H "Authorization: Bearer $TOKEN"
```

Expected: `nginx: configuration file /usr/local/openresty/nginx/conf/nginx.conf test is successful` (wording may differ slightly) and the grep shows `proxy_pass https://megoopm_upstream_<B>;`. If the login fails, ask the user for the admin credentials rather than guessing.

- [ ] **Step 8: Document**

In `docs/data-model.md`, add to the tables list after the `proxy_hosts` row:

```markdown
| `proxy_host_locations` | Extra `location ^~ <path>` routes of a proxy host to other pools. |
```

and two rows to the "Foreign keys & cascade rules" table after the `proxy_hosts.access_list_id` row:

```markdown
| `proxy_host_locations.proxy_host_id` → `proxy_hosts.id` | **CASCADE** | Locations belong to their host. |
| `proxy_host_locations.upstream_id` → `upstreams.id` | **RESTRICT** | A pool used by a location cannot be deleted. |
```

In `docs/nginx-engine.md`, extend the `megoopm-proxy-{id}.conf` bullet under "Generated files":

```markdown
  Extra per-path routes (`proxy_host_locations`) render as `location ^~ <path>`
  blocks pointing at their own pool; `^~` makes the longest matching prefix win
  over the asset-caching regex location, so `/api/app.js` reaches the API pool.
  Host-wide options (websockets, forwarded headers, auth stripping) apply to
  every location; a location whose pool has no backends is omitted.
```

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/nginx backend/app/templates/nginx/server.conf.j2 backend/tests/test_nginx_render.py backend/tests/test_proxy_hosts_api.py docs/data-model.md docs/nginx-engine.md
git commit -m "feat(nginx): render per-path locations as ^~ prefix blocks" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Frontend helpers — form state, validation, payload

**Files:**
- Modify: `frontend/src/components/proxy-hosts/lib.ts`
- Create: `frontend/src/components/proxy-hosts/test-utils.ts` (shared `makeHost` fixture; not a test file, so vitest never collects it)
- Test: `frontend/src/components/proxy-hosts/lib.test.ts`

**Interfaces:**
- Consumes: regenerated `ProxyHost` / `ProxyHostCreate` types (Task 3) — `ProxyHost.locations: {id, path, upstream_id, forward_scheme}[]`.
- Produces (all exported from `lib.ts`):
  ```ts
  export const NO_ACCESS_LIST = "none"; export const NO_CERTIFICATE = "none";
  export type DialogTab = "forwarding" | "certificate" | "advanced";
  export const TOGGLE_KEYS = ["ssl_forced","http2_support","hsts_enabled","hsts_subdomains","caching_enabled","block_exploits","allow_websocket_upgrade"] as const;
  export type ToggleKey = (typeof TOGGLE_KEYS)[number];
  export interface LocationRow { key: string; path: string; upstreamId: string; scheme: HttpScheme }
  export interface ProxyHostFormState { domains: string[]; accessListId: string; enabled: boolean; rootUpstreamId: string; rootScheme: HttpScheme; locations: LocationRow[]; certificateId: string; toggles: Record<ToggleKey, boolean>; advancedConfig: string }
  export interface FormError { message: string; tab: DialogTab | null }
  export function newLocationRow(): LocationRow
  export function emptyToggles(): Record<ToggleKey, boolean>
  export function stateFromHost(host: ProxyHost | null | undefined): ProxyHostFormState
  export function validateLocations(rows: LocationRow[]): FormError | null
  export function validateForm(form: ProxyHostFormState): FormError | null
  export function buildPayload(form: ProxyHostFormState, host: ProxyHost | null | undefined): ProxyHostCreate
  ```

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/proxy-hosts/test-utils.ts`:

```ts
import type { ProxyHost } from "@/lib/api";

/** A fully-populated ProxyHost row for tests; override any field via `patch`. */
export function makeHost(patch: Partial<ProxyHost> = {}): ProxyHost {
  return {
    id: 1,
    domain_names: ["app.example.com"],
    upstream_id: 1,
    forward_scheme: "http",
    certificate_id: null,
    access_list_id: null,
    ssl_forced: false,
    http2_support: false,
    hsts_enabled: false,
    hsts_subdomains: false,
    caching_enabled: false,
    block_exploits: false,
    allow_websocket_upgrade: false,
    crowdsec_enabled: false,
    crowdsec_appsec_enabled: false,
    advanced_config: "",
    enabled: true,
    locations: [],
    created_at: "2026-08-27T00:00:00Z",
    updated_at: "2026-08-27T00:00:00Z",
    ...patch,
  };
}
```

Append to `frontend/src/components/proxy-hosts/lib.test.ts` (extend the existing import from `@/components/proxy-hosts/lib` with the new names):

```ts
import {
  NO_CERTIFICATE,
  buildPayload,
  newLocationRow,
  stateFromHost,
  validateForm,
  validateLocations,
  type LocationRow,
} from "@/components/proxy-hosts/lib";
import { makeHost } from "@/components/proxy-hosts/test-utils";

function row(patch: Partial<LocationRow>): LocationRow {
  return { ...newLocationRow(), path: "/api/", upstreamId: "2", scheme: "http", ...patch };
}

describe("validateLocations", () => {
  it("accepts distinct prefixed paths with pools", () => {
    expect(validateLocations([row({}), row({ path: "/api" })])).toBeNull();
  });

  it.each([
    [row({ path: "api" }), "must start with /"],
    [row({ path: "/" }), "root"],
    [row({ path: "/a b" }), "whitespace"],
    [row({ path: '/a"b' }), "whitespace"],
    [row({ path: "/" + "x".repeat(255) }), "255"],
    [row({ upstreamId: "" }), "Select an upstream pool for /api/"],
  ])("rejects %j", (bad, fragment) => {
    const err = validateLocations([bad]);
    expect(err?.tab).toBe("forwarding");
    expect(err?.message).toContain(fragment);
  });

  it("rejects duplicate paths", () => {
    expect(validateLocations([row({}), row({})])?.message).toContain("Duplicate location path");
  });
});

describe("validateForm", () => {
  it("checks domains, then the root pool, then locations", () => {
    const base = stateFromHost(makeHost());
    expect(validateForm({ ...base, domains: [] })).toEqual({
      message: "Enter at least one domain name.",
      tab: null,
    });
    expect(validateForm({ ...base, rootUpstreamId: "" })).toEqual({
      message: "Select an upstream pool to forward to.",
      tab: "forwarding",
    });
    expect(validateForm({ ...base, locations: [row({ path: "bad" })] })?.tab).toBe("forwarding");
    expect(validateForm(base)).toBeNull();
  });
});

describe("stateFromHost / buildPayload", () => {
  it("round-trips a host with a certificate and locations", () => {
    const host = makeHost({
      certificate_id: 7,
      ssl_forced: true,
      locations: [{ id: 5, path: "/api/", upstream_id: 2, forward_scheme: "https" }],
    });
    const form = stateFromHost(host);
    expect(form.certificateId).toBe("7");
    expect(form.locations).toEqual([
      { key: "loc-5", path: "/api/", upstreamId: "2", scheme: "https" },
    ]);
    expect(buildPayload(form, host)).toMatchObject({
      domain_names: ["app.example.com"],
      upstream_id: 1,
      forward_scheme: "http",
      certificate_id: 7,
      access_list_id: null,
      ssl_forced: true,
      locations: [{ path: "/api/", upstream_id: 2, forward_scheme: "https" }],
    });
  });

  it("sends null for no certificate and trims location paths", () => {
    const form = { ...stateFromHost(null), rootUpstreamId: "1", domains: ["a.com"] };
    form.locations = [row({ path: " /ws " })];
    expect(form.certificateId).toBe(NO_CERTIFICATE);
    const payload = buildPayload(form, null);
    expect(payload.certificate_id).toBeNull();
    expect(payload.locations).toEqual([{ path: "/ws", upstream_id: 2, forward_scheme: "http" }]);
    expect(payload.crowdsec_enabled).toBe(false);
  });

  it("passes CrowdSec flags through from the existing host", () => {
    const host = makeHost({ crowdsec_enabled: true, crowdsec_appsec_enabled: true });
    expect(buildPayload(stateFromHost(host), host)).toMatchObject({
      crowdsec_enabled: true,
      crowdsec_appsec_enabled: true,
    });
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (in `frontend/`): `npm test -- src/components/proxy-hosts/lib.test.ts`
Expected: FAIL — `validateLocations is not a function` / type errors on the missing exports.

- [ ] **Step 3: Implement the helpers**

Append to `frontend/src/components/proxy-hosts/lib.ts` (add `import type { HttpScheme, ProxyHost, ProxyHostCreate } from "@/lib/api";` at the top):

```ts
// --- Proxy host dialog form model ------------------------------------------

/** Sentinel Select values for "nothing attached" (`null` on the wire). */
export const NO_ACCESS_LIST = "none";
export const NO_CERTIFICATE = "none";

export type DialogTab = "forwarding" | "certificate" | "advanced";

export const TOGGLE_KEYS = [
  "ssl_forced",
  "http2_support",
  "hsts_enabled",
  "hsts_subdomains",
  "caching_enabled",
  "block_exploits",
  "allow_websocket_upgrade",
] as const;
export type ToggleKey = (typeof TOGGLE_KEYS)[number];

export interface LocationRow {
  /** Stable React key; `loc-<id>` for stored rows, `loc-new-<n>` for new ones. */
  key: string;
  path: string;
  /** Pool id as a Select value; "" while unset. */
  upstreamId: string;
  scheme: HttpScheme;
}

export interface ProxyHostFormState {
  domains: string[];
  accessListId: string;
  enabled: boolean;
  rootUpstreamId: string;
  rootScheme: HttpScheme;
  locations: LocationRow[];
  certificateId: string;
  toggles: Record<ToggleKey, boolean>;
  advancedConfig: string;
}

/** A validation failure and the tab that holds the offending field (`null` = outside tabs). */
export interface FormError {
  message: string;
  tab: DialogTab | null;
}

let newRowSeq = 0;

export function newLocationRow(): LocationRow {
  newRowSeq += 1;
  return { key: `loc-new-${newRowSeq}`, path: "", upstreamId: "", scheme: "http" };
}

export function emptyToggles(): Record<ToggleKey, boolean> {
  return Object.fromEntries(TOGGLE_KEYS.map((k) => [k, false])) as Record<ToggleKey, boolean>;
}

export function stateFromHost(host: ProxyHost | null | undefined): ProxyHostFormState {
  if (!host) {
    return {
      domains: [],
      accessListId: NO_ACCESS_LIST,
      enabled: true,
      rootUpstreamId: "",
      rootScheme: "http",
      locations: [],
      certificateId: NO_CERTIFICATE,
      toggles: emptyToggles(),
      advancedConfig: "",
    };
  }
  return {
    domains: [...host.domain_names],
    accessListId: host.access_list_id ? String(host.access_list_id) : NO_ACCESS_LIST,
    enabled: host.enabled ?? true,
    rootUpstreamId: String(host.upstream_id),
    rootScheme: host.forward_scheme,
    locations: (host.locations ?? []).map((l) => ({
      key: `loc-${l.id}`,
      path: l.path,
      upstreamId: String(l.upstream_id),
      scheme: l.forward_scheme ?? "http",
    })),
    certificateId: host.certificate_id ? String(host.certificate_id) : NO_CERTIFICATE,
    toggles: Object.fromEntries(
      TOGGLE_KEYS.map((k) => [k, host[k] ?? false]),
    ) as Record<ToggleKey, boolean>,
    advancedConfig: host.advanced_config ?? "",
  };
}

const LOCATION_FORBIDDEN = /[\s{};"]/;

/** Mirrors the backend path rules so mistakes are caught before the request. */
export function validateLocations(rows: LocationRow[]): FormError | null {
  const seen = new Set<string>();
  for (const row of rows) {
    const path = row.path.trim();
    let message: string | null = null;
    if (!path.startsWith("/")) message = `Location path "${path}" must start with /.`;
    else if (path === "/") message = "/ is the root route — add a sub-path such as /api/.";
    else if (LOCATION_FORBIDDEN.test(path))
      message = `Location path "${path}" must not contain whitespace or { } ; ".`;
    else if (path.length > 255) message = "Location paths are limited to 255 characters.";
    else if (seen.has(path)) message = `Duplicate location path "${path}".`;
    else if (!row.upstreamId) message = `Select an upstream pool for ${path}.`;
    if (message) return { message, tab: "forwarding" };
    seen.add(path);
  }
  return null;
}

export function validateForm(form: ProxyHostFormState): FormError | null {
  if (form.domains.length === 0) return { message: "Enter at least one domain name.", tab: null };
  if (!form.rootUpstreamId)
    return { message: "Select an upstream pool to forward to.", tab: "forwarding" };
  return validateLocations(form.locations);
}

function idOrNull(value: string, sentinel: string): number | null {
  return value === sentinel ? null : Number.parseInt(value, 10);
}

export function buildPayload(
  form: ProxyHostFormState,
  host: ProxyHost | null | undefined,
): ProxyHostCreate {
  return {
    domain_names: form.domains,
    upstream_id: Number.parseInt(form.rootUpstreamId, 10),
    forward_scheme: form.rootScheme,
    certificate_id: idOrNull(form.certificateId, NO_CERTIFICATE),
    access_list_id: idOrNull(form.accessListId, NO_ACCESS_LIST),
    enabled: form.enabled,
    advanced_config: form.advancedConfig,
    ...form.toggles,
    locations: form.locations.map((row) => ({
      path: row.path.trim(),
      upstream_id: Number.parseInt(row.upstreamId, 10),
      forward_scheme: row.scheme,
    })),
    // CrowdSec enforcement is owned by the Security UI (MEG-22); pass the
    // existing values through untouched so this form never clobbers them.
    crowdsec_enabled: host?.crowdsec_enabled ?? false,
    crowdsec_appsec_enabled: host?.crowdsec_appsec_enabled ?? false,
  };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run (in `frontend/`): `npm test -- src/components/proxy-hosts/lib.test.ts`
Expected: PASS

Run: `npm run lint && npm run typecheck`
Expected: clean

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/proxy-hosts/lib.ts frontend/src/components/proxy-hosts/lib.test.ts frontend/src/components/proxy-hosts/test-utils.ts
git commit -m "feat(ui): proxy host form helpers with location validation" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: `LocationsEditor` component

**Files:**
- Create: `frontend/src/components/proxy-hosts/locations-editor.tsx`

**Interfaces:**
- Consumes: `LocationRow`, `newLocationRow` (Task 5); `Upstream`, `HttpScheme`, `HTTP_SCHEMES` from `@/lib/api`.
- Produces:
  ```ts
  export function LocationsEditor(props: {
    rootUpstreamId: string; rootScheme: HttpScheme;
    onRootChange: (patch: { rootUpstreamId?: string; rootScheme?: HttpScheme }) => void;
    rows: LocationRow[]; onRowsChange: (rows: LocationRow[]) => void;
    pools: Upstream[]; disabled: boolean;
  }): JSX.Element
  ```
  Accessible names used by tests: inputs `aria-label="Location path"`, selects `aria-label="Upstream pool"` / `aria-label="Forward scheme"`, buttons `Add location` and `aria-label="Remove location"`.

- [ ] **Step 1: Create the component**

Create `frontend/src/components/proxy-hosts/locations-editor.tsx`:

```tsx
"use client";

import { Plus, Trash2 } from "lucide-react";

import { HTTP_SCHEMES, type HttpScheme, type Upstream } from "@/lib/api";
import { newLocationRow, type LocationRow } from "@/components/proxy-hosts/lib";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const SCHEME_LABELS: Record<HttpScheme, string> = { http: "http", https: "https" };

function PoolSelect({
  value,
  onChange,
  pools,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  pools: Upstream[];
  disabled: boolean;
}) {
  const noPools = pools.length === 0;
  return (
    <Select value={value} onValueChange={(v) => onChange(v as string)}>
      <SelectTrigger aria-label="Upstream pool" disabled={disabled || noPools}>
        <SelectValue placeholder={noPools ? "No pools — create one first" : "Select a pool"} />
      </SelectTrigger>
      <SelectContent>
        {pools.map((pool) => (
          <SelectItem key={pool.id} value={String(pool.id)}>
            {pool.name} ({pool.backends?.length ?? 0} backends)
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function SchemeSelect({
  value,
  onChange,
  disabled,
}: {
  value: HttpScheme;
  onChange: (value: HttpScheme) => void;
  disabled: boolean;
}) {
  return (
    <Select value={value} onValueChange={(v) => onChange(v as HttpScheme)} items={SCHEME_LABELS}>
      <SelectTrigger aria-label="Forward scheme" disabled={disabled}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {HTTP_SCHEMES.map((scheme) => (
          <SelectItem key={scheme} value={scheme}>
            {SCHEME_LABELS[scheme]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/**
 * Root route (pinned to `/`) plus extra `location ^~ <path>` rows, each
 * forwarding to its own upstream pool with its own scheme.
 */
export function LocationsEditor({
  rootUpstreamId,
  rootScheme,
  onRootChange,
  rows,
  onRowsChange,
  pools,
  disabled,
}: {
  rootUpstreamId: string;
  rootScheme: HttpScheme;
  onRootChange: (patch: { rootUpstreamId?: string; rootScheme?: HttpScheme }) => void;
  rows: LocationRow[];
  onRowsChange: (rows: LocationRow[]) => void;
  pools: Upstream[];
  disabled: boolean;
}) {
  function updateRow(key: string, patch: Partial<LocationRow>) {
    onRowsChange(rows.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">Locations</h3>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => onRowsChange([...rows, newLocationRow()])}
          disabled={disabled}
        >
          <Plus /> Add location
        </Button>
      </div>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-40">Path</TableHead>
              <TableHead>Upstream pool</TableHead>
              <TableHead className="w-28">Scheme</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow>
              <TableCell>
                <Input aria-label="Root path" value="/" readOnly disabled className="font-mono" />
              </TableCell>
              <TableCell>
                <PoolSelect
                  value={rootUpstreamId}
                  onChange={(v) => onRootChange({ rootUpstreamId: v })}
                  pools={pools}
                  disabled={disabled}
                />
              </TableCell>
              <TableCell>
                <SchemeSelect
                  value={rootScheme}
                  onChange={(v) => onRootChange({ rootScheme: v })}
                  disabled={disabled}
                />
              </TableCell>
              <TableCell />
            </TableRow>
            {rows.map((row) => (
              <TableRow key={row.key}>
                <TableCell>
                  <Input
                    aria-label="Location path"
                    value={row.path}
                    onChange={(e) => updateRow(row.key, { path: e.target.value })}
                    placeholder="/api/"
                    className="font-mono"
                    disabled={disabled}
                  />
                </TableCell>
                <TableCell>
                  <PoolSelect
                    value={row.upstreamId}
                    onChange={(v) => updateRow(row.key, { upstreamId: v })}
                    pools={pools}
                    disabled={disabled}
                  />
                </TableCell>
                <TableCell>
                  <SchemeSelect
                    value={row.scheme}
                    onChange={(v) => updateRow(row.key, { scheme: v })}
                    disabled={disabled}
                  />
                </TableCell>
                <TableCell>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    aria-label="Remove location"
                    onClick={() => onRowsChange(rows.filter((r) => r.key !== row.key))}
                    disabled={disabled}
                  >
                    <Trash2 />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <p className="text-xs text-muted-foreground">
        <code>/</code> is the host&apos;s root route. Extra rows are prefix matches
        (<code>location ^~</code>) — the longest matching path wins.
      </p>
    </div>
  );
}
```

- [ ] **Step 2: Type-check and lint**

Run (in `frontend/`): `npm run lint && npm run typecheck`
Expected: clean (the component is exercised by Task 7's dialog test).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/proxy-hosts/locations-editor.tsx
git commit -m "feat(ui): LocationsEditor for proxy host routes" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Tabbed proxy host dialog and view wiring

**Files:**
- Modify: `frontend/src/components/proxy-hosts/proxy-host-dialog.tsx` (rewrite)
- Modify: `frontend/src/components/proxy-hosts/proxy-hosts-view.tsx:60-100, 342-350`
- Test: `frontend/src/components/proxy-hosts/proxy-host-dialog.test.tsx` (new)

**Interfaces:**
- Consumes: everything from Task 5 (`stateFromHost`, `validateForm`, `buildPayload`, `NO_*`, `TOGGLE_KEYS`, `DialogTab`), `LocationsEditor` (Task 6), `certificates.list()` and `Certificate` from `@/lib/api`.
- Produces: `ProxyHostDialog` props gain `certs: Certificate[]`; tab triggers are `role="tab"` named `Forwarding`, `Certificate`, `Advanced`; certificate select `id="host-certificate"` labelled `Certificate`; TLS toggles labelled `Force SSL`, `HTTP/2`, `HSTS`, `HSTS subdomains`.

- [ ] **Step 1: Write the failing dialog test**

Create `frontend/src/components/proxy-hosts/proxy-host-dialog.test.tsx`:

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { proxyHosts, type Upstream } from "@/lib/api";
import { ProxyHostDialog } from "@/components/proxy-hosts/proxy-host-dialog";
import { makeHost } from "@/components/proxy-hosts/test-utils";

const pools: Upstream[] = [
  {
    id: 1,
    name: "app-pool",
    description: "",
    lb_method: "round_robin",
    enabled: true,
    backends: [],
    created_at: "2026-08-27T00:00:00Z",
    updated_at: "2026-08-27T00:00:00Z",
  },
  {
    id: 2,
    name: "api-pool",
    description: "",
    lb_method: "round_robin",
    enabled: true,
    backends: [],
    created_at: "2026-08-27T00:00:00Z",
    updated_at: "2026-08-27T00:00:00Z",
  },
];

function renderDialog(host = makeHost()) {
  return render(
    <ProxyHostDialog
      open
      onOpenChange={() => {}}
      host={host}
      pools={pools}
      lists={[]}
      certs={[]}
      onSaved={() => {}}
    />,
  );
}

describe("ProxyHostDialog", () => {
  beforeEach(() => {
    vi.spyOn(proxyHosts, "update").mockResolvedValue(makeHost());
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("keeps domains/access list/enabled outside three tabs", () => {
    renderDialog();
    expect(screen.getByLabelText("Domain names")).toBeInTheDocument();
    expect(screen.getByLabelText("Access list")).toBeInTheDocument();
    expect(screen.getByLabelText("Enabled")).toBeInTheDocument();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Forwarding",
      "Certificate",
      "Advanced",
    ]);
    expect(screen.getByRole("button", { name: "Add location" })).toBeInTheDocument();
  });

  it("disables the TLS toggles while no certificate is selected", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("tab", { name: "Certificate" }));
    expect(screen.getByLabelText("Certificate")).toBeInTheDocument();
    for (const name of ["Force SSL", "HTTP/2", "HSTS", "HSTS subdomains"]) {
      expect(screen.getByLabelText(name)).toBeDisabled();
    }
  });

  it("enables the TLS toggles when the host has a certificate", async () => {
    const user = userEvent.setup();
    renderDialog(makeHost({ certificate_id: 7 }));
    await user.click(screen.getByRole("tab", { name: "Certificate" }));
    expect(screen.getByLabelText("Force SSL")).toBeEnabled();
  });

  it("jumps to the Forwarding tab and reports a bad location on save", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: "Add location" }));
    await user.type(screen.getByLabelText("Location path"), "api");
    await user.click(screen.getByRole("tab", { name: "Advanced" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent('Location path "api" must start with /.');
    expect(screen.getByRole("tab", { name: "Forwarding" })).toHaveAttribute("aria-selected", "true");
    expect(proxyHosts.update).not.toHaveBeenCalled();
  });

  it("saves locations and the certificate in the payload", async () => {
    const user = userEvent.setup();
    renderDialog(
      makeHost({
        certificate_id: 7,
        locations: [{ id: 5, path: "/api/", upstream_id: 2, forward_scheme: "https" }],
      }),
    );
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(proxyHosts.update).toHaveBeenCalledTimes(1));
    expect(vi.mocked(proxyHosts.update).mock.calls[0][1]).toMatchObject({
      upstream_id: 1,
      certificate_id: 7,
      locations: [{ path: "/api/", upstream_id: 2, forward_scheme: "https" }],
    });
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run (in `frontend/`): `npm test -- src/components/proxy-hosts/proxy-host-dialog.test.tsx`
Expected: FAIL — no `role="tab"` elements / `certs` prop type error / "Add location" not found.

- [ ] **Step 3: Rewrite the dialog**

Replace `frontend/src/components/proxy-hosts/proxy-host-dialog.tsx` with:

```tsx
"use client";

import { useState } from "react";
import { toast } from "sonner";

import {
  proxyHosts,
  type AccessList,
  type Certificate,
  type ProxyHost,
  type Upstream,
} from "@/lib/api";
import {
  NO_ACCESS_LIST,
  NO_CERTIFICATE,
  buildPayload,
  describeError,
  stateFromHost,
  validateForm,
  type DialogTab,
  type ProxyHostFormState,
  type ToggleKey,
} from "@/components/proxy-hosts/lib";
import { LocationsEditor } from "@/components/proxy-hosts/locations-editor";
import { DomainTagsInput } from "@/components/domains/domain-tags-input";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

type ToggleDef = readonly [ToggleKey, string, string];

const FORWARDING_TOGGLES: readonly ToggleDef[] = [
  ["caching_enabled", "Cache assets", "Cache static assets"],
  ["block_exploits", "Block exploits", "Block common exploit probes"],
  ["allow_websocket_upgrade", "Websockets", "Pass Upgrade/Connection headers"],
];

const TLS_TOGGLES: readonly ToggleDef[] = [
  ["ssl_forced", "Force SSL", "Redirect :80 to HTTPS"],
  ["http2_support", "HTTP/2", "Enable HTTP/2 on the TLS listener"],
  ["hsts_enabled", "HSTS", "Emit a Strict-Transport-Security header"],
  ["hsts_subdomains", "HSTS subdomains", "Include subdomains in HSTS"],
];

function ToggleGrid({
  defs,
  values,
  disabled,
  onChange,
}: {
  defs: readonly ToggleDef[];
  values: Record<ToggleKey, boolean>;
  disabled: boolean;
  onChange: (key: ToggleKey, value: boolean) => void;
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {defs.map(([key, label, hint]) => (
        <label key={key} className="flex items-start gap-2">
          <Switch
            aria-label={label}
            checked={values[key]}
            onCheckedChange={(v) => onChange(key, v)}
            disabled={disabled}
          />
          <span className="space-y-0.5">
            <span className="block text-sm font-medium leading-none">{label}</span>
            <span className="block text-xs text-muted-foreground">{hint}</span>
          </span>
        </label>
      ))}
    </div>
  );
}

export function ProxyHostDialog({
  open,
  onOpenChange,
  host,
  pools,
  lists,
  certs,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  host?: ProxyHost | null;
  pools: Upstream[];
  lists: AccessList[];
  certs: Certificate[];
  onSaved: () => void;
}) {
  const isEdit = Boolean(host);
  // Seeded from props on mount; the parent remounts this dialog (keyed) per
  // target, so no reset-on-open effect is needed.
  const [form, setForm] = useState<ProxyHostFormState>(() => stateFromHost(host));
  const [tab, setTab] = useState<DialogTab>("forwarding");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [domainsInvalid, setDomainsInvalid] = useState(false);

  function patch(changes: Partial<ProxyHostFormState>) {
    setForm((prev) => ({ ...prev, ...changes }));
  }

  function setToggle(key: ToggleKey, value: boolean) {
    setForm((prev) => ({ ...prev, toggles: { ...prev.toggles, [key]: value } }));
  }

  async function handleSubmit() {
    setError(null);
    if (domainsInvalid) {
      setError("Fix the highlighted domain first.");
      return;
    }
    const problem = validateForm(form);
    if (problem) {
      if (problem.tab) setTab(problem.tab);
      setError(problem.message);
      return;
    }
    const payload = buildPayload(form, host);

    setSaving(true);
    try {
      if (isEdit && host) {
        await proxyHosts.update(host.id, payload);
      } else {
        await proxyHosts.create(payload);
      }
      toast.success(isEdit ? "Proxy host updated" : "Proxy host created");
      onOpenChange(false);
      onSaved();
    } catch (err) {
      const described = describeError(err);
      setError(described.message);
      toast.error(described.message);
    } finally {
      setSaving(false);
    }
  }

  const noPools = pools.length === 0;
  const noCertificate = form.certificateId === NO_CERTIFICATE;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit proxy host" : "New proxy host"}</DialogTitle>
          <DialogDescription>
            Terminate domain names and forward matching traffic to upstream pools.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="host-domains">Domain names</Label>
            <DomainTagsInput
              id="host-domains"
              value={form.domains}
              onChange={(domains) => patch({ domains })}
              onPendingInvalidChange={setDomainsInvalid}
              placeholder="example.com"
              disabled={saving}
            />
            <p className="text-xs text-muted-foreground">
              Press Enter or comma after each domain. Wildcards like <code>*.example.com</code>{" "}
              are allowed.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="host-access-list">Access list</Label>
            <Select
              value={form.accessListId}
              onValueChange={(value) => patch({ accessListId: value as string })}
            >
              <SelectTrigger id="host-access-list" disabled={saving}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_ACCESS_LIST}>None (public)</SelectItem>
                {lists.map((list) => (
                  <SelectItem key={list.id} value={String(list.id)}>
                    {list.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <label className="flex items-start gap-2 self-end pb-2">
            <Switch
              aria-label="Enabled"
              checked={form.enabled}
              onCheckedChange={(v) => patch({ enabled: v })}
              disabled={saving}
            />
            <span className="space-y-0.5">
              <span className="block text-sm font-medium leading-none">Enabled</span>
              <span className="block text-xs text-muted-foreground">
                Disabled hosts are excluded from the nginx config
              </span>
            </span>
          </label>
        </div>

        <Tabs value={tab} onValueChange={(value) => setTab(value as DialogTab)}>
          <TabsList>
            <TabsTab value="forwarding">Forwarding</TabsTab>
            <TabsTab value="certificate">Certificate</TabsTab>
            <TabsTab value="advanced">Advanced</TabsTab>
          </TabsList>

          <TabsPanel value="forwarding" className="space-y-4 pt-2">
            <ToggleGrid
              defs={FORWARDING_TOGGLES}
              values={form.toggles}
              disabled={saving}
              onChange={setToggle}
            />
            <LocationsEditor
              rootUpstreamId={form.rootUpstreamId}
              rootScheme={form.rootScheme}
              onRootChange={patch}
              rows={form.locations}
              onRowsChange={(locations) => patch({ locations })}
              pools={pools}
              disabled={saving}
            />
          </TabsPanel>

          <TabsPanel value="certificate" className="space-y-4 pt-2">
            <div className="space-y-1.5">
              <Label htmlFor="host-certificate">Certificate</Label>
              <Select
                value={form.certificateId}
                onValueChange={(value) => patch({ certificateId: value as string })}
              >
                <SelectTrigger id="host-certificate" disabled={saving}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NO_CERTIFICATE}>None (HTTP only)</SelectItem>
                  {certs.map((cert) => (
                    <SelectItem
                      key={cert.id}
                      value={String(cert.id)}
                      disabled={cert.status !== "active"}
                    >
                      {cert.name}
                      {cert.status !== "active" ? ` — ${cert.status}` : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                Without a certificate the host serves plain HTTP on :80 and the options below
                have no effect.
              </p>
            </div>
            <ToggleGrid
              defs={TLS_TOGGLES}
              values={form.toggles}
              disabled={saving || noCertificate}
              onChange={setToggle}
            />
          </TabsPanel>

          <TabsPanel value="advanced" className="space-y-1.5 pt-2">
            <Label htmlFor="host-advanced">Advanced nginx config</Label>
            <Textarea
              id="host-advanced"
              value={form.advancedConfig}
              onChange={(e) => patch({ advancedConfig: e.target.value })}
              placeholder="# Raw directives injected into the server block"
              className="font-mono text-xs"
              disabled={saving}
            />
          </TabsPanel>
        </Tabs>

        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={saving || noPools}>
            {saving ? "Saving…" : isEdit ? "Save changes" : "Create host"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

Notes for the implementer:
- Every switch carries an explicit `aria-label` (the wrapping `<label>` also contains the hint text, so its text content alone would not match `getByLabelText("Enabled")` exactly). If the `Access list` select trigger isn't found by `getByLabelText("Access list")`, keep `id="host-access-list"` on `SelectTrigger` (it renders a button, which `<Label htmlFor>` labels).
- If `SelectItem` does not accept `disabled` in `components/ui/select.tsx`, add `disabled?: boolean` pass-through to the underlying base-ui `Select.Item` there (one-line change).
- base-ui `Tabs.Root` `onValueChange` receives `(value, eventDetails)`; the cast above is enough.

- [ ] **Step 4: Wire the view**

In `frontend/src/components/proxy-hosts/proxy-hosts-view.tsx`:
- Extend the `@/lib/api` import with `certificates` and `type Certificate`.
- Add state: `const [certs, setCerts] = useState<Certificate[]>([]);`
- In `load`, change the `Promise.all` to four calls and set the fourth:
  ```ts
      const [h, p, a, c] = await Promise.all([
        proxyHosts.list(),
        upstreams.list(),
        accessLists.list(),
        certificates.list(),
      ]);
      setHosts(h);
      setPools(p);
      setLists(a);
      setCerts(c);
  ```
- Pass `certs={certs}` to `<ProxyHostDialog ... />`.

- [ ] **Step 5: Run the tests and gates**

Run (in `frontend/`): `npm test`
Expected: PASS (the new dialog test plus everything else).

If `user.click` on a tab does not switch panels under jsdom (the "Force SSL" lookup fails), render the Tabs with `keepMounted` on each `TabsPanel` is **not** the fix — instead assert with `screen.findByLabelText("Force SSL")` (async) to let base-ui finish its state update. If it still fails, report it rather than weakening the assertion.

Run: `npm run lint && npm run typecheck`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/proxy-hosts/proxy-host-dialog.tsx frontend/src/components/proxy-hosts/proxy-hosts-view.tsx frontend/src/components/proxy-hosts/proxy-host-dialog.test.tsx frontend/src/components/ui/select.tsx
git commit -m "feat(ui): tabbed proxy host dialog with certificate picker and locations" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

(Drop `select.tsx` from the `git add` if it was not changed.)

---

### Task 8: Full verification and live check

**Files:** none new.

- [ ] **Step 1: Backend suite, lint, migration check**

```bash
docker exec megoopm-test python -m pytest -q -p no:warnings
docker exec megoopm-test sh -c 'python -m ruff check . && alembic check'
```
Expected: all pass, `No new upgrade operations detected.`

- [ ] **Step 2: Frontend gates**

Run (in `frontend/`): `npm run lint && npm run typecheck && npm test`
Expected: clean

- [ ] **Step 3: Rebuild and eyeball the dialog**

```bash
cd /c/Projects/MegooPM && docker compose build backend worker beat frontend && docker compose up -d backend worker beat frontend
```

Then, in the browser at the frontend URL (`FRONTEND_PORT`, default 3000): Proxy hosts → New proxy host. Confirm: Domain names / Access list / Enabled sit above the tabs; Forwarding shows the three switches then the Locations table with the pinned `/` row; Certificate shows the dropdown with "None (HTTP only)" and the four greyed-out switches; Advanced shows the textarea. Create a host with an extra `/api/` location, then run `docker exec megoopm-nginx openresty -t` and delete the test host again.

- [ ] **Step 4: Remove the test container**

```bash
docker rm -f megoopm-test
```

- [ ] **Step 5: Push**

```bash
git push origin main
```
