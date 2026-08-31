# Proxy Host Forward Target Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a proxy host, and each of its locations, forward to a single `host:port` instead of requiring an upstream pool.

**Architecture:** `upstream_id` becomes nullable on `proxy_hosts` and `proxy_host_locations`, each gaining `forward_host`/`forward_port` and a check constraint enforcing exactly one target. A single `_target()` helper in the renderer resolves either shape into the string `proxy_pass` already takes, so the template needs no new branching. The loader's existing "skip a host whose pool is missing" rule becomes conditional on a pool actually being the target.

**Tech Stack:** FastAPI + SQLAlchemy 2 + Alembic (backend), Next.js 15 + React 19 + base-ui (frontend), pytest + vitest, Jinja2 nginx templates.

**Spec:** `docs/superpowers/specs/2026-08-31-proxy-host-forward-target-design.md`

**Deviation from the spec:** the spec sketches one migration covering both tables, but its phasing ships them separately. This plan splits it into `0014_host_forward_target` (proxy_hosts) and `0015_location_forward_target` (proxy_host_locations) so each phase stands alone.

## Global Constraints

- **Backend tests only run on Linux** — `app` imports `fcntl`. Run in a throwaway container:
  ```bash
  MSYS_NO_PATHCONV=1 docker run --rm --user root -v "C:/Projects/megoopm/backend:/src:ro" \
    --entrypoint sh megoopm-backend:latest -c '
    cp -r /src /work && cd /work && pip install -q --no-input "pytest>=8.2" "pytest-asyncio>=0.23" "aiosqlite>=0.20" "ruff>=0.6" >/dev/null 2>&1
    python -m ruff check . && python -m pytest -q -p no:warnings'
  ```
- **Anything touching `proxy_hosts` needs real Postgres.** `domain_names` is a Postgres `ARRAY` the SQLite engine cannot render, which is why `test_proxy_hosts_api.py` is gated. Add `-e DATABASE_URL=postgresql+asyncpg://megoopm:megoopm@127.0.0.1:55440/megoopm --network host` to the command above, with:
  ```bash
  docker run -d --name ph-db -e POSTGRES_USER=megoopm -e POSTGRES_PASSWORD=megoopm \
    -e POSTGRES_DB=megoopm -p 55440:5432 postgres:16-alpine && sleep 9
  ```
- **Frontend from `frontend/`**: `npx vitest run`, `npx eslint src`, `npx tsc --noEmit`. **vitest does not typecheck** — a green suite with a broken build is possible, so always run `tsc`.
- **Schema changes need two regenerations**, or `test_openapi.py` fails and the frontend types drift:
  ```bash
  MSYS_NO_PATHCONV=1 docker run --rm --user root -v "C:/Projects/megoopm/backend:/src" -w /src \
    --entrypoint sh megoopm-backend:latest -c "python -m scripts.export_openapi"
  cd frontend && npm run gen:api
  ```
- **`op.drop_constraint` takes the BARE constraint name.** The metadata convention (`ck_%(table_name)s_%(constraint_name)s`) is applied on top; passing the expanded name yields `ck_proxy_hosts_ck_proxy_hosts_...` and fails.
- **Line endings must stay LF.** Check `git ls-files --eol <file>` after editing; rewrite with `newline="\n"` if it reports `w/crlf`.
- **Migration head is `0013_stream_upstream`.**

## File Structure

**Phase 1 — root route**
- Create `backend/alembic/versions/0014_host_forward_target.py`
- Create `backend/tests/test_host_forward_target.py`, `backend/tests/test_host_forward_target_pg.py`
- Modify `backend/app/models/proxy_host.py`, `schemas/proxy_host.py`, `services/proxy_host.py`
- Modify `backend/app/services/nginx/state.py`, `renderer.py`, `loader.py`, `templates/nginx/server.conf.j2`
- Modify `frontend/src/components/proxy-hosts/lib.ts` (+ `lib.test.ts`), `proxy-host-dialog.tsx`, `proxy-hosts-view.tsx`

**Phase 2 — locations**
- Create `backend/alembic/versions/0015_location_forward_target.py`
- Modify the same model/schema/spec/renderer files for `ProxyHostLocation`
- Modify `frontend/src/components/proxy-hosts/locations-editor.tsx`

**Phase 3 — docs**
- Modify `backend/app/models/upstream.py` (docstring), `docs/data-model.md`, `docs/nginx-engine.md`

---

# Phase 1 — Root route

## Task 1: `proxy_hosts` gains a host target

**Files:**
- Modify: `backend/app/models/proxy_host.py`
- Create: `backend/alembic/versions/0014_host_forward_target.py`
- Create: `backend/tests/test_host_forward_target.py`

**Interfaces:**
- Produces: `ProxyHost.upstream_id: int | None`, `ProxyHost.forward_host: str | None`, `ProxyHost.forward_port: int | None`, constraint `host_target_exactly_one`.

- [ ] **Step 1: Write the failing test**

`proxy_hosts.domain_names` is a Postgres ARRAY, so this file uses a **table subset** that avoids it — we are testing the constraint, not the model's full DDL. Create `backend/tests/test_host_forward_target.py`:

```python
"""A proxy host forwards to exactly one of a host:port or an upstream pool."""

from __future__ import annotations

import pytest
from app.models.enums import UpstreamContext  # noqa: F401  (registers the enum)
from app.models.proxy_host import ProxyHost
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


@pytest.fixture
def engine(tmp_path):
    # ARRAY has no SQLite compiler; render it as JSON for this table only.
    from sqlalchemy.dialects.postgresql import ARRAY
    from sqlalchemy.ext.compiler import compiles

    @compiles(ARRAY, "sqlite")
    def _array_as_json(type_, compiler, **kw):  # noqa: ANN001, ANN202
        return "JSON"

    engine = create_engine(f"sqlite:///{tmp_path / 'ph.db'}", future=True)
    ProxyHost.__table__.create(engine)
    return engine


def test_rejects_both_targets(engine) -> None:
    with Session(engine) as s, pytest.raises(IntegrityError):
        s.add(ProxyHost(domain_names=["a.example.com"], upstream_id=1,
                        forward_host="h", forward_port=8080))
        s.commit()


def test_rejects_neither_target(engine) -> None:
    with Session(engine) as s, pytest.raises(IntegrityError):
        s.add(ProxyHost(domain_names=["a.example.com"]))
        s.commit()


def test_accepts_a_host_target(engine) -> None:
    with Session(engine) as s:
        s.add(ProxyHost(domain_names=["a.example.com"], forward_host="10.0.0.1",
                        forward_port=8080))
        s.commit()
        row = s.query(ProxyHost).one()
        assert (row.forward_host, row.forward_port) == ("10.0.0.1", 8080)
        assert row.upstream_id is None


def test_accepts_a_pool_target(engine) -> None:
    """Today's shape keeps working; there is no data migration."""
    with Session(engine) as s:
        s.add(ProxyHost(domain_names=["a.example.com"], upstream_id=3))
        s.commit()
        assert s.query(ProxyHost).one().upstream_id == 3
```

- [ ] **Step 2: Run it and watch it fail**

Use the Global Constraints backend command, scoped to `tests/test_host_forward_target.py`.
Expected: FAIL — `ProxyHost` has no `forward_host`.

- [ ] **Step 3: Update the model**

In `app/models/proxy_host.py`, make `upstream_id` nullable and add the two columns:

```python
    # Either a pool (weighted balancing, passive failover)...
    upstream_id: Mapped[int | None] = mapped_column(
        ForeignKey("upstreams.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    # ...or a single backend. Exactly one; see host_target_exactly_one.
    forward_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    forward_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

Keep the existing `ForeignKey` arguments exactly as they are apart from
`nullable`. Add to `__table_args__`:

```python
        CheckConstraint(
            "forward_port IS NULL OR forward_port BETWEEN 1 AND 65535",
            name="forward_port_range",
        ),
        CheckConstraint(
            "(forward_host IS NOT NULL AND forward_port IS NOT NULL AND upstream_id IS NULL)"
            " OR (forward_host IS NULL AND forward_port IS NULL AND upstream_id IS NOT NULL)",
            name="host_target_exactly_one",
        ),
```

Update the class docstring, which says the host forwards to a pool.

- [ ] **Step 4: Run the tests**

Expected: PASS (4 tests).

- [ ] **Step 5: Write the migration**

Create `backend/alembic/versions/0014_host_forward_target.py`, `down_revision = "0013_stream_upstream"`:

```python
"""Proxy hosts may forward to a single host:port instead of a pool

upstream_id becomes nullable and forward_host/forward_port are added, with a
check constraint enforcing exactly one target.

DOWNGRADE DELETES PROXY HOSTS. Restoring NOT NULL requires removing every
host-targeted row, and on this table that is the vhost itself, not a detail of
one — the sites those hosts serve stop being served. Take a backup first.

Revision ID: 0014_host_forward_target
Revises: 0013_stream_upstream
Create Date: 2026-08-31 12:00:00.000000
"""
```

`upgrade()`: `alter_column("proxy_hosts", "upstream_id", nullable=True)`; `add_column` both new columns; `create_check_constraint("forward_port_range", ...)` and `create_check_constraint("host_target_exactly_one", ...)` with the SQL from Step 3.

`downgrade()`: drop both check constraints by **bare** name, `DELETE FROM proxy_hosts WHERE upstream_id IS NULL`, drop both columns, then `alter_column(..., nullable=False)`.

- [ ] **Step 6: Round-trip the migration on real Postgres**

```bash
docker run -d --name ph-db -e POSTGRES_USER=megoopm -e POSTGRES_PASSWORD=megoopm \
  -e POSTGRES_DB=megoopm -p 55440:5432 postgres:16-alpine && sleep 9
MSYS_NO_PATHCONV=1 docker run --rm --network host -v "C:/Projects/megoopm/backend:/app" -w /app \
  -e DATABASE_URL=postgresql+asyncpg://megoopm:megoopm@127.0.0.1:55440/megoopm -e SECRET_KEY=test \
  --entrypoint sh megoopm-backend:latest -c 'alembic upgrade head && alembic downgrade 0013_stream_upstream && alembic upgrade head'
```
Expected: all three succeed. Then seed one pool-targeted and one host-targeted row, downgrade, and confirm **only the host-targeted row was deleted** — the destructive path is the one worth proving.

- [ ] **Step 7: Commit**

```bash
git add backend && git commit -m "feat(proxy-hosts): allow a single host:port forward target"
```

## Task 2: Schemas and validation

**Files:**
- Modify: `backend/app/schemas/proxy_host.py`, `backend/app/services/proxy_host.py`
- Modify: `backend/tests/test_host_forward_target.py`

**Interfaces:**
- Consumes: the model from Task 1.
- Produces: `ProxyHostBase.forward_host`, `.forward_port`, optional `.upstream_id`; a `_require_exactly_one_target` validator.

- [ ] **Step 1: Write the failing tests**

```python
from app.schemas.proxy_host import ProxyHostCreate


def _body(**kw):
    base = {"domain_names": ["a.example.com"]}
    base.update(kw)
    return ProxyHostCreate(**base)


def test_schema_rejects_both_targets() -> None:
    with pytest.raises(ValueError, match="either a forward host"):
        _body(upstream_id=1, forward_host="h", forward_port=8080)


def test_schema_rejects_neither_target() -> None:
    with pytest.raises(ValueError, match="either a forward host"):
        _body()


def test_schema_rejects_a_half_specified_host() -> None:
    """A host with no port is not a target; it must not slip through as one."""
    with pytest.raises(ValueError, match="either a forward host"):
        _body(forward_host="h")


def test_schema_accepts_either_target() -> None:
    assert _body(upstream_id=1).upstream_id == 1
    assert _body(forward_host="h", forward_port=8080).forward_port == 8080
```

- [ ] **Step 2: Run and watch them fail**

Expected: FAIL — `upstream_id` is still required, so a different error is raised.

- [ ] **Step 3: Update the schemas**

In `ProxyHostBase`, make `upstream_id` `int | None = Field(default=None, ...)` and add:

```python
    forward_host: str | None = Field(
        default=None, min_length=1, max_length=255,
        description="Single backend host; null when forwarding to a pool",
    )
    forward_port: int | None = Field(
        default=None, ge=1, le=65535,
        description="Single backend port; null when forwarding to a pool",
    )

    @model_validator(mode="after")
    def _require_exactly_one_target(self) -> ProxyHostBase:
        """Mirrors the DB constraint so the API answers 422, not a 500."""
        host_target = self.forward_host is not None and self.forward_port is not None
        if host_target == (self.upstream_id is not None):
            raise ValueError("Set either a forward host and port, or an upstream pool.")
        return self
```

Add `forward_host` / `forward_port` to `ProxyHostUpdate` as optional. Update the module docstring, which says a host "forwards matching traffic to an upstream pool (`upstream_id`)".

- [ ] **Step 4: Make the pool check conditional**

In `app/services/proxy_host.py`, `create_proxy_host` currently calls
`_assert_pools_usable(db, {values["upstream_id"]}, what="upstream")` unconditionally.
Guard it — there is no pool to check for a host target:

```python
    if values.get("upstream_id") is not None:
        await _assert_pools_usable(db, {values["upstream_id"]}, what="upstream")
```

The update path already guards on `new_upstream is not None`, so it needs no change.

- [ ] **Step 5: Regenerate schemas, run everything**

Run both regeneration commands from Global Constraints, then the full backend suite against Postgres.
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend frontend/src/lib/api/generated && \
  git commit -m "feat(proxy-hosts): validate exactly one forward target"
```

## Task 3: Render either target

**Files:**
- Modify: `backend/app/services/nginx/state.py`, `renderer.py`, `templates/nginx/server.conf.j2`
- Modify: `backend/tests/test_nginx_render.py`

**Interfaces:**
- Produces: `ProxyHostSpec.forward_host`, `.forward_port`, optional `.upstream_id`; `renderer._target(spec) -> str`.

- [ ] **Step 1: Write the failing test**

```python
def test_host_target_renders_a_literal_backend() -> None:
    host = ProxyHostSpec(
        id=1, domain_names=("a.example.com",), forward_host="10.0.0.1", forward_port=8080
    )
    out = render_config(DesiredState(proxy_hosts=(host,)))["megoopm-proxy-1.conf"]
    assert "proxy_pass http://10.0.0.1:8080;" in out


def test_pool_target_is_unchanged() -> None:
    host = ProxyHostSpec(id=1, domain_names=("a.example.com",), upstream_id=1)
    out = render_config(DesiredState(proxy_hosts=(host,), http_upstreams=(_pool(),)))
    assert "proxy_pass http://megoopm_upstream_1;" in out["megoopm-proxy-1.conf"]
```

- [ ] **Step 2: Run and watch it fail**

Expected: FAIL — `ProxyHostSpec` requires `upstream_id`.

- [ ] **Step 3: Update the spec and renderer**

`ProxyHostSpec`: `upstream_id: int | None = None`, plus `forward_host: str | None = None` and `forward_port: int | None = None`. Update the class docstring.

In `renderer.py`:

```python
def _target(spec: ProxyHostSpec | LocationSpec) -> str:
    """The proxy_pass destination: a pool name, or a literal host:port.

    The template's proxy_block macro never inspects this — it only interpolates
    it after the scheme — so both shapes flow through one code path.
    """
    if spec.upstream_id is not None:
        return pool_name(spec.upstream_id)
    return f"{spec.forward_host}:{spec.forward_port}"
```

and in `_render_proxy_host`, replace `pool_name=pool_name(host.upstream_id)` with `pool_name=_target(host)`.

- [ ] **Step 4: Rename the template parameter**

In `server.conf.j2`, rename the macro's third parameter from `pool` to `target` (`proxy_block(path, scheme, target, modifier="")`), update its two uses inside the macro body and the two call sites, and rename the `pool_name` variable passed from the renderer to `target`. **No new branching** — the macro already ignores what kind of value it got.

- [ ] **Step 5: Run the tests**

Expected: PASS, and every pre-existing render test still passes — the pool path must be byte-identical.

- [ ] **Step 6: Commit**

```bash
git add backend && git commit -m "feat(nginx): render a proxy host's literal backend target"
```

## Task 4: The loader must not drop host-targeted hosts

This is the highest-risk change in the plan and gets its own task so it gets its own review. Getting it wrong means hosts silently vanish from the config and their sites stop being served, with no error anywhere.

**Files:**
- Modify: `backend/app/services/nginx/loader.py`
- Create: `backend/tests/test_host_forward_target_pg.py`

- [ ] **Step 1: Write the failing test**

Postgres-gated, using the `pg_session` fixture pattern from `tests/test_upstream_context_pg.py` (copy its `_pg_available` probe and fixture verbatim, changing the probe to `SELECT forward_host FROM proxy_hosts LIMIT 0`).

```python
async def test_host_targeted_host_is_included(pg_session: AsyncSession) -> None:
    """The regression this feature is most likely to introduce.

    The loader drops any host whose pool is missing. A host-targeted host has no
    pool by design, so an unguarded check removes it from the render entirely —
    the site stops being served and nothing reports an error.
    """
    await proxy_host_service.create_proxy_host(
        pg_session,
        {"domain_names": ["a.example.com"], "forward_host": "10.0.0.1", "forward_port": 8080},
    )

    state = await load_desired_state(pg_session)

    assert [h.forward_host for h in state.proxy_hosts] == ["10.0.0.1"]
    assert state.http_upstreams == ()


async def test_pool_targeted_host_with_an_empty_pool_is_still_skipped(
    pg_session: AsyncSession,
) -> None:
    pool = Upstream(name="empty", context=UpstreamContext.http)
    pg_session.add(pool)
    await pg_session.flush()
    await proxy_host_service.create_proxy_host(
        pg_session, {"domain_names": ["b.example.com"], "upstream_id": pool.id}
    )

    state = await load_desired_state(pg_session)

    assert state.proxy_hosts == ()
```

- [ ] **Step 2: Run and watch the first test fail**

Start `ph-db` per Global Constraints and run with `DATABASE_URL` set.
Expected: FAIL — `state.proxy_hosts` is empty because the host was skipped.

- [ ] **Step 3: Make the skip conditional**

In `loader.py`, the loop currently opens:

```python
    for host in hosts:
        pool = host.upstream
        if pool is None or not pool.enabled:
            continue  # nothing healthy to forward to
        if pool.id not in upstreams:
            upstreams[pool.id] = _upstream_spec(pool)
        if not upstreams[pool.id].backends:
            continue  # empty pool → skip host rather than emit an invalid block
```

Guard the whole block on a pool being the target:

```python
    for host in hosts:
        if host.upstream_id is not None:
            pool = host.upstream
            if pool is None or not pool.enabled:
                continue  # nothing healthy to forward to
            if pool.id not in upstreams:
                upstreams[pool.id] = _upstream_spec(pool)
            if not upstreams[pool.id].backends:
                continue  # empty pool → skip host rather than emit an invalid block
```

Pass `forward_host=host.forward_host, forward_port=host.forward_port` into the
`ProxyHostSpec`. The `referenced` set that builds `http_upstreams` must skip
`None`:

```python
    referenced = {h.upstream_id for h in host_specs if h.upstream_id is not None}
```

- [ ] **Step 4: Run the tests**

Expected: PASS both — the host-targeted host is included, and the empty-pool skip still works.

- [ ] **Step 5: Run the whole backend suite against Postgres**

The pre-existing `test_proxy_hosts_api.py` exercises this loader heavily; it passing is the real check.

- [ ] **Step 6: Commit**

```bash
git add backend && git commit -m "fix(nginx): keep host-targeted proxy hosts in the render"
```

## Task 5: Form state and payload (React-free)

`lib.ts` is unit-tested without rendering, so the logic lands and is proven before any JSX changes.

**Files:**
- Modify: `frontend/src/components/proxy-hosts/lib.ts`, `lib.test.ts`

**Interfaces:**
- Produces: `ProxyHostFormState.rootTargetMode: "host" | "pool"`, `.rootForwardHost: string`, `.rootForwardPort: string`.

- [ ] **Step 1: Write the failing tests**

```typescript
it("defaults a new host to the pool target", () => {
  expect(stateFromHost(null).rootTargetMode).toBe("pool");
});

it("opens on the mode the host actually uses", () => {
  const host = makeHost({ upstream_id: null, forward_host: "10.0.0.1", forward_port: 8080 });
  const state = stateFromHost(host);
  expect(state.rootTargetMode).toBe("host");
  expect(state.rootForwardHost).toBe("10.0.0.1");
  expect(state.rootForwardPort).toBe("8080");
});

it("sends exactly one target", () => {
  const base = stateFromHost(null);
  const pool = buildPayload({ ...base, domains: ["a.example.com"], rootUpstreamId: "2" }, null);
  expect(pool.upstream_id).toBe(2);
  expect(pool.forward_host).toBeNull();

  const host = buildPayload(
    { ...base, domains: ["a.example.com"], rootTargetMode: "host",
      rootForwardHost: "10.0.0.1", rootForwardPort: "8080" },
    null,
  );
  expect(host.upstream_id).toBeNull();
  expect(host.forward_host).toBe("10.0.0.1");
  expect(host.forward_port).toBe(8080);
});

it("requires a host and port in host mode", () => {
  const form = { ...stateFromHost(null), domains: ["a.example.com"], rootTargetMode: "host" as const };
  expect(validateForm(form)?.message).toMatch(/forward host/i);
});

it("still requires a pool in pool mode", () => {
  const form = { ...stateFromHost(null), domains: ["a.example.com"] };
  expect(validateForm(form)?.message).toMatch(/pool/i);
});
```

- [ ] **Step 2: Run and watch them fail**

```bash
cd frontend && npx vitest run src/components/proxy-hosts/lib.test.ts
```
Expected: FAIL — `rootTargetMode` does not exist.

- [ ] **Step 3: Implement**

Add the three fields to `ProxyHostFormState`. In `stateFromHost`, default
`rootTargetMode` to `"pool"` for a new host and derive it from
`host.upstream_id != null ? "pool" : "host"` when editing, seeding
`rootForwardHost`/`rootForwardPort` from the host (empty strings when absent).

In `validateForm`, replace the unconditional pool check:

```typescript
  if (form.rootTargetMode === "pool") {
    if (!form.rootUpstreamId)
      return { message: "Select an upstream pool to forward to.", tab: "forwarding" };
  } else {
    if (!form.rootForwardHost.trim())
      return { message: "Enter a forward host.", tab: "forwarding" };
    if (parsePort(form.rootForwardPort) === null)
      return { message: "Forward port must be between 1 and 65535.", tab: "forwarding" };
  }
```

`parsePort` already exists in `components/streams/lib.ts`; move it to a shared
module or duplicate the four-line function rather than importing streams code
into proxy-hosts.

In `buildPayload`, send exactly one target and explicitly null the other, so
switching an existing host's mode clears the old value:

```typescript
  const usingPool = form.rootTargetMode === "pool";
  upstream_id: usingPool ? Number.parseInt(form.rootUpstreamId, 10) : null,
  forward_host: usingPool ? null : form.rootForwardHost.trim(),
  forward_port: usingPool ? null : parsePort(form.rootForwardPort),
```

- [ ] **Step 4: Run the tests**

Expected: PASS. Then `npx tsc --noEmit` — the dialog still passes the old state shape and must be updated to compile.

- [ ] **Step 5: Commit**

```bash
git add frontend/src && git commit -m "feat(ui): model a proxy host's forward target in form state"
```

## Task 6: Dialog control and list column

**Files:**
- Modify: `frontend/src/components/proxy-hosts/proxy-host-dialog.tsx`, `proxy-hosts-view.tsx`
- Modify: `frontend/src/components/proxy-hosts/proxy-host-dialog.test.tsx`, `proxy-hosts-view.test.tsx`

- [ ] **Step 1: Write the failing tests**

```tsx
it("defaults a new host to Pool", () => {
  renderDialog(null);
  expect(screen.getByLabelText("Upstream pool")).toBeInTheDocument();
  expect(screen.queryByLabelText("Forward host")).not.toBeInTheDocument();
});

it("switches to a single host", async () => {
  const user = userEvent.setup();
  renderDialog(null);
  await user.click(screen.getByRole("radio", { name: "Single host" }));
  expect(screen.getByLabelText("Forward host")).toBeInTheDocument();
  expect(screen.queryByLabelText("Upstream pool")).not.toBeInTheDocument();
});

it("keeps typed values across a mode round trip", async () => {
  const user = userEvent.setup();
  renderDialog(null);
  await user.click(screen.getByRole("radio", { name: "Single host" }));
  await user.type(screen.getByLabelText("Forward host"), "10.0.0.1");
  await user.click(screen.getByRole("radio", { name: "Pool" }));
  await user.click(screen.getByRole("radio", { name: "Single host" }));
  expect(screen.getByLabelText("Forward host")).toHaveValue("10.0.0.1");
});
```

and for the list:

```tsx
it("shows a literal backend for a host-targeted row", async () => {
  vi.spyOn(proxyHosts, "list").mockResolvedValue([
    makeHost({ upstream_id: null, forward_host: "10.0.0.1", forward_port: 8080 }),
  ]);
  mount();
  expect(await screen.findByText("10.0.0.1:8080")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run and watch them fail**

Expected: FAIL — no radios; the list renders an empty Upstream cell.

- [ ] **Step 3: Implement the dialog control**

On the Forwarding tab, above the target fields, add the same two-option radio
group the stream dialog uses (`role="radiogroup"`, per-input
`aria-label={"Single host" | "Pool"}`), driven by `form.rootTargetMode`. Render
the existing pool `Select` in pool mode, or a Forward host / Forward port pair in
host mode. Both modes' values already live in form state from Task 5, so
switching preserves input with no extra work.

- [ ] **Step 4: Implement the list cell**

In `proxy-hosts-view.tsx`, the Upstream cell currently resolves
`poolsById.get(host.upstream_id)`. Make it:

```tsx
host.upstream_id != null
  ? poolsById.get(host.upstream_id)?.name ?? "—"
  : `${host.forward_host}:${host.forward_port}`
```

Keep `poolsById` — it is still needed for pool-targeted rows.

- [ ] **Step 5: Run everything**

```bash
npx vitest run && npx eslint src && npx tsc --noEmit
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/src && git commit -m "feat(ui): choose a single host or a pool on a proxy host"
```

---

# Phase 2 — Locations

## Task 7: `proxy_host_locations` gains a host target

**Files:**
- Modify: `backend/app/models/proxy_host.py`
- Create: `backend/alembic/versions/0015_location_forward_target.py`
- Modify: `backend/tests/test_host_forward_target.py`

- [ ] **Step 1: Write the failing tests**

Mirror Task 1's four tests against `ProxyHostLocation` (which has no ARRAY column, so it needs no compiler shim), using `proxy_host_id=1` and a path:

```python
def test_location_rejects_both_targets(loc_engine) -> None:
    with Session(loc_engine) as s, pytest.raises(IntegrityError):
        s.add(ProxyHostLocation(proxy_host_id=1, path="/api", upstream_id=1,
                                forward_host="h", forward_port=8080))
        s.commit()
```

plus neither-target, host-target and pool-target cases.

- [ ] **Step 2: Run and watch them fail**

Expected: FAIL — `ProxyHostLocation` has no `forward_host`.

- [ ] **Step 3: Update the model and write migration `0015`**

The same three column changes and two constraints as Task 1, named
`location_target_exactly_one` and `forward_port_range`, with
`down_revision = "0014_host_forward_target"`. The downgrade deletes
host-targeted **locations** — less severe than Task 1's, since a location is a
detail of a host rather than a vhost, but still stated in the docstring.

- [ ] **Step 4: Round-trip on Postgres and commit**

```bash
git add backend && git commit -m "feat(proxy-hosts): allow a single host:port target on a location"
```

## Task 8: Render location targets

**Files:**
- Modify: `backend/app/services/nginx/state.py`, `renderer.py`, `loader.py`, `templates/nginx/server.conf.j2`
- Modify: `backend/tests/test_nginx_render.py`

- [ ] **Step 1: Write the failing test**

```python
def test_locations_render_both_target_kinds() -> None:
    host = ProxyHostSpec(
        id=1,
        domain_names=("a.example.com",),
        upstream_id=1,
        locations=(
            LocationSpec(path="/api", upstream_id=2),
            LocationSpec(path="/img", forward_host="10.0.0.9", forward_port=9000),
        ),
    )
    out = render_config(
        DesiredState(proxy_hosts=(host,), http_upstreams=(_pool(1), _pool(2)))
    )["megoopm-proxy-1.conf"]
    assert "proxy_pass http://megoopm_upstream_2;" in out
    assert "proxy_pass http://10.0.0.9:9000;" in out
```

- [ ] **Step 2: Run and watch it fail**

Expected: FAIL — `LocationSpec` requires `upstream_id`.

- [ ] **Step 3: Implement**

`LocationSpec`: `upstream_id: int | None = None`, plus `forward_host` / `forward_port`. In `_render_proxy_host`, replace the `location_pools` dict with

```python
        location_targets={loc.path: _target(loc) for loc in host.locations},
```

keyed by **path**, not `upstream_id` — two host-targeted locations have no id to
key on, and two locations can now share neither. Update the template's location
call site to `location_targets[loc.path]`.

In the loader, guard the location pool lookup the same way Task 4 guarded the
host's, and include location pool ids in `referenced` only when non-`None`.

- [ ] **Step 4: Run everything, commit**

```bash
git add backend && git commit -m "feat(nginx): render per-location literal backends"
```

## Task 9: Location row state

**Files:**
- Modify: `frontend/src/components/proxy-hosts/lib.ts`, `lib.test.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
it("defaults a new location row to the pool target", () => {
  expect(newLocationRow().targetMode).toBe("pool");
});

it("validates each row by its own mode", () => {
  const rows = [
    { ...newLocationRow(), path: "/api", targetMode: "host" as const, forwardHost: "" },
    { ...newLocationRow(), path: "/img", upstreamId: "2" },
  ];
  expect(validateLocations(rows)?.message).toMatch(/forward host/i);
});

it("sends one target per row", () => {
  const rows = [
    { ...newLocationRow(), path: "/api", upstreamId: "2" },
    { ...newLocationRow(), path: "/img", targetMode: "host" as const,
      forwardHost: "10.0.0.9", forwardPort: "9000" },
  ];
  const out = buildPayload({ ...stateFromHost(null), domains: ["a.example.com"],
    rootUpstreamId: "1", locations: rows }, null);
  expect(out.locations[0]).toMatchObject({ upstream_id: 2, forward_host: null });
  expect(out.locations[1]).toMatchObject({ upstream_id: null, forward_host: "10.0.0.9" });
});
```

- [ ] **Step 2: Run and watch them fail**

Expected: FAIL — `LocationRow` has no `targetMode`.

- [ ] **Step 3: Implement**

`LocationRow` gains `targetMode: "host" | "pool"`, `forwardHost: string`,
`forwardPort: string`; `newLocationRow()` returns `targetMode: "pool"` with empty
strings. `validateLocations` branches per row exactly as `validateForm` does for
the root. `buildPayload`'s location mapping nulls the inactive side per row.

- [ ] **Step 4: Run the tests and commit**

```bash
git add frontend/src && git commit -m "feat(ui): model a per-location forward target"
```

## Task 10: Locations editor UI

**Files:**
- Modify: `frontend/src/components/proxy-hosts/locations-editor.tsx`
- Modify: `frontend/src/components/proxy-hosts/proxy-host-dialog.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
it("switches one location row's mode without touching its siblings", async () => {
  const user = userEvent.setup();
  renderDialog(makeHost());
  await user.click(screen.getByRole("button", { name: "Add location" }));
  await user.click(screen.getByRole("button", { name: "Add location" }));

  const rows = screen.getAllByRole("radio", { name: "Single host" });
  await user.click(rows[0]);

  // Exactly one row flipped: two pool selects became one.
  expect(screen.getAllByLabelText("Upstream pool")).toHaveLength(1);
  expect(screen.getAllByLabelText("Forward host")).toHaveLength(1);
});
```

- [ ] **Step 2: Run and watch it fail**

Expected: FAIL — no per-row radios.

- [ ] **Step 3: Implement**

Each row renders the same two-option radio group, then either its `PoolSelect`
or a Forward host / Forward port pair. The radio `name` attribute must be
**unique per row** (`name={\`loc-target-${row.key}\`}`) — sharing one name across
rows makes the browser treat every row's radios as a single group, so selecting a
mode on one row deselects it on all the others. That is the bug this task is most
likely to ship.

- [ ] **Step 4: Run everything, commit**

```bash
npx vitest run && npx eslint src && npx tsc --noEmit
git add frontend/src && git commit -m "feat(ui): choose a target per location row"
```

---

# Phase 3 — Documentation

## Task 11: Correct the docs the feature contradicts

**Files:**
- Modify: `backend/app/models/upstream.py`, `docs/data-model.md`, `docs/nginx-engine.md`

- [ ] **Step 1: Rewrite the positioning claim**

`upstream.py`'s module docstring says pools-always is "the defining feature of
MegooPM over stock Nginx Proxy Manager ... rather than a single forward
host/port". Replace with wording that keeps pools as the *richer default*
without asserting they are mandatory, since they no longer are.

- [ ] **Step 2: Update the reference docs**

`docs/data-model.md`: `proxy_hosts` and `proxy_host_locations` rows in the
constraint list gain the either/or target; the FK table notes `upstream_id` is
now nullable on both. `docs/nginx-engine.md`: note that a `server` block's
`proxy_pass` targets either a pool name or a literal `host:port`.

- [ ] **Step 3: Commit**

```bash
git add backend docs && git commit -m "docs: proxy hosts may forward to a single backend"
```

---

## Self-review notes

- **Spec coverage:** data model → Tasks 1, 7; validation → Task 2; renderer →
  Tasks 3, 8; loader → Tasks 4, 8; frontend → Tasks 5, 6, 9, 10; docs → Task 11;
  phasing → the phase headings.
- **Type consistency:** `_target`, `rootTargetMode`, `rootForwardHost`,
  `rootForwardPort`, `LocationRow.targetMode`, `location_targets`,
  `host_target_exactly_one`, `location_target_exactly_one` are each defined once
  and referenced by that name later.
- **Deliberate deviation from the spec:** two migrations rather than one, so each
  phase ships independently. Recorded in the header.
- **Two traps called out where they bite**, both learned the hard way on the
  stream work: `drop_constraint` takes the bare name, and vitest does not
  typecheck so `tsc` must be run separately. A third is new here: the per-row
  radio `name` collision in Task 10.
- **Known thin spot:** Task 6 Step 3 describes the dialog's radio group rather
  than reproducing its JSX, because the stream dialog's version is the reference
  and copying it verbatim into the plan would drift from that file. The tests in
  that step pin the behaviour exactly.
