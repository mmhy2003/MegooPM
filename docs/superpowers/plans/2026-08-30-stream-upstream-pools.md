# Stream Upstream Pools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a stream forward to an upstream pool instead of a single `host:port`, giving TCP/UDP forwards the same weighted balancing and failover proxy hosts already have.

**Architecture:** Pools gain a `context` column (`http` / `stream` / `both`) that decides which nginx context they render into. `DesiredState.upstreams` splits into `http_upstreams` and `stream_upstreams` so the renderer cannot emit a stream-only pool into `http {}`. Streams gain a nullable `upstream_id` and target either a `host:port` or a pool, enforced by a DB check constraint. Pools move onto their own `/upstreams` route because a pool that can back a stream is no longer a proxy-host concept.

**Tech Stack:** FastAPI + SQLAlchemy 2 + Alembic + Celery (backend), Next.js 15 + React 19 + base-ui + Tailwind (frontend), pytest + vitest, Jinja2 nginx templates.

**Spec:** `docs/superpowers/specs/2026-08-30-stream-upstream-pools-design.md`

## Global Constraints

- **Backend tests only run on Linux** — `app` imports `fcntl`. Run everything in a throwaway container:
  ```bash
  MSYS_NO_PATHCONV=1 docker run --rm --user root -v "C:/Projects/megoopm/backend:/src:ro" \
    --entrypoint sh megoopm-backend:latest -c "
    cp -r /src /work && cd /work && pip install -q --no-input 'pytest>=8.2' 'pytest-asyncio>=0.23' 'aiosqlite>=0.20' 'ruff>=0.6'
    python -m pytest -q -p no:warnings && python -m ruff check ."
  ```
- **Frontend commands run from `frontend/`**: `npx vitest run`, `npx eslint src`, `npx tsc --noEmit`.
- **Line endings must stay LF.** The Edit tool has written CRLF in this repo before. After editing, check `git ls-files --eol <file>`; if it shows `w/crlf`, rewrite the file with `newline="\n"`.
- **`openapi.json` is snapshot-tested.** Any schema change requires regenerating it or `tests/test_openapi.py` fails:
  ```bash
  MSYS_NO_PATHCONV=1 docker run --rm --user root -v "C:/Projects/megoopm/backend:/src" -w /src \
    --entrypoint sh megoopm-backend:latest -c "python -m scripts.export_openapi"
  ```
- **Migration head is `0011_cluster_sweep`.** New migrations chain `0012_upstream_context` → `0013_stream_upstream`.
- **Enum columns use `values_callable`** so the value, not the member name, lands in Postgres. Follow `LoadBalanceMethod` in `app/models/enums.py`.
- **base-ui switches** render `<span role="switch" aria-disabled>` — assert `aria-disabled`, never the `disabled` attribute.
- **Every config-affecting write** goes through `after_config_write`, which audits and enqueues an nginx reload. Do not bypass it.

## File Structure

**Phase 1 — pools get their own page**
- Create `frontend/src/app/(app)/upstreams/page.tsx` — route shell only.
- Create `frontend/src/components/upstreams/upstreams-view.tsx` — the pools table, extracted.
- Move `frontend/src/components/proxy-hosts/upstream-dialog.tsx` → `frontend/src/components/upstreams/upstream-dialog.tsx`.
- Create `frontend/src/components/upstreams/upstreams-view.test.tsx` — pool tests relocated.
- Modify `frontend/src/config/nav.ts` and `nav.test.ts`.
- Modify `frontend/src/components/proxy-hosts/proxy-hosts-view.tsx` (drop pools + `Tabs`) and `proxy-hosts-view.test.tsx`.

**Phase 2 — pool context**
- Modify `backend/app/models/enums.py` (`UpstreamContext`), `models/upstream.py`, `schemas/upstream.py`, `services/upstream.py`.
- Create `backend/alembic/versions/0012_upstream_context.py`.
- Modify `backend/app/services/nginx/state.py`, `loader.py`, `renderer.py` (the `DesiredState` split).
- Modify `frontend/src/components/upstreams/upstream-dialog.tsx`.
- Create `backend/tests/test_upstream_context.py`.

**Phase 3 — stream pools**
- Create `backend/alembic/versions/0013_stream_upstream.py`.
- Modify `backend/app/models/stream.py`, `schemas/stream.py`, `services/stream.py`, `api/routes/upstreams.py`.
- Modify `backend/app/services/nginx/state.py`, `loader.py`, `renderer.py`, `templates/nginx/stream.conf.j2`.
- Modify `frontend/src/components/streams/stream-dialog.tsx` and its test.
- Create `backend/tests/test_stream_pools.py`.

Each phase ends in a shippable state. Stopping after Phase 1 or Phase 2 leaves a working app.

---

# Phase 1 — Move pools to their own page

## Task 1: Create the `/upstreams` page

Pools appear in two places after this task (the new page and the existing proxy-hosts tab). Task 2 removes the duplicate. Splitting it this way keeps each task independently testable.

**Files:**
- Create: `frontend/src/app/(app)/upstreams/page.tsx`
- Create: `frontend/src/components/upstreams/upstreams-view.tsx`
- Create: `frontend/src/components/upstreams/upstreams-view.test.tsx`
- Move: `frontend/src/components/proxy-hosts/upstream-dialog.tsx` → `frontend/src/components/upstreams/upstream-dialog.tsx`
- Modify: `frontend/src/config/nav.ts`, `frontend/src/config/nav.test.ts`

**Interfaces:**
- Produces: `UpstreamsView` (no props), default-exported page at `/upstreams`, `UpstreamDialog` re-homed at `@/components/upstreams/upstream-dialog`.

- [ ] **Step 1: Update the nav test to expect the new entry**

`nav.test.ts` asserts the exact title list. Add `"Upstream Pools"` after `"Proxy Hosts"`:

```ts
expect(titles).toEqual([
  "Proxy Hosts",
  "Upstream Pools",
  "Certificates",
  "Access Lists",
  "Streams",
  "Redirection Hosts",
  "404 Hosts",
  "Security",
  "Users",
]);
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd frontend && npx vitest run src/config/nav.test.ts
```
Expected: FAIL — received list has no `"Upstream Pools"`.

- [ ] **Step 3: Add the nav entry**

In `nav.ts`, add `Server` to the lucide import block, then insert after the Proxy Hosts entry:

```ts
  {
    title: "Upstream Pools",
    href: "/upstreams",
    icon: Server,
    description: "Backend server pools that proxy hosts and streams forward to.",
  },
```

- [ ] **Step 4: Run the nav test**

```bash
npx vitest run src/config/nav.test.ts
```
Expected: PASS.

- [ ] **Step 5: Move the dialog**

```bash
cd /c/Projects/megoopm
git mv frontend/src/components/proxy-hosts/upstream-dialog.tsx frontend/src/components/upstreams/upstream-dialog.tsx
```

Update the import inside it if it references `./lib` — `describeError` lives at `@/components/proxy-hosts/lib` and stays there.

- [ ] **Step 6: Create the view by extracting the pools half**

Create `frontend/src/components/upstreams/upstreams-view.tsx`. Copy from `proxy-hosts-view.tsx`: the `LoadingRows` helper, the pools state (`pools`, `poolDialog`, `deletePool`), the `load`/`refresh` callbacks reduced to `upstreams.list()` only, `setPoolEnabled`, the pools table currently at lines 285–367, and the two dialogs at lines 382–414. Give it the page header treatment the other views use:

```tsx
<div className="flex items-center gap-3">
  <div className="flex size-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
    <Server className="size-5" />
  </div>
  <div>
    <h2 className="text-xl font-semibold tracking-tight">Upstream Pools</h2>
    <p className="text-sm text-muted-foreground">
      Backend server pools that proxy hosts and streams forward to.
    </p>
  </div>
</div>
```

Drop the `Tabs` wrapper — this page has one table.

- [ ] **Step 7: Create the route**

```tsx
import { UpstreamsView } from "@/components/upstreams/upstreams-view";

export default function UpstreamsPage() {
  return <UpstreamsView />;
}
```

Match the shape of `frontend/src/app/(app)/streams/page.tsx` exactly, including any metadata export it carries.

- [ ] **Step 8: Move the pool tests**

Create `upstreams-view.test.tsx` with the two pool tests currently in `proxy-hosts-view.test.tsx` (`toggles an upstream pool`, `reverts a pool toggle that fails`), rewritten without the `openPools` tab helper since there is no tab any more:

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { upstreams, type Upstream } from "@/lib/api";
import { UpstreamsView } from "@/components/upstreams/upstreams-view";

function makePool(over: Partial<Upstream> = {}): Upstream {
  return {
    id: 1,
    name: "app-pool",
    description: "",
    lb_method: "round_robin",
    enabled: true,
    backends: [],
    created_at: "2026-08-27T00:00:00Z",
    updated_at: "2026-08-27T00:00:00Z",
    ...over,
  };
}

describe("UpstreamsView", () => {
  beforeEach(() => {
    vi.spyOn(toast, "error").mockImplementation(() => "" as never);
    vi.spyOn(upstreams, "list").mockResolvedValue([makePool()]);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("toggles a pool", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(upstreams, "update").mockResolvedValue(makePool({ enabled: false }));
    render(<UpstreamsView />);

    await user.click(await screen.findByLabelText("Enable app-pool"));

    await waitFor(() => expect(update).toHaveBeenCalledWith(1, { enabled: false }));
  });

  it("reverts a toggle that fails", async () => {
    const user = userEvent.setup();
    vi.spyOn(upstreams, "update").mockRejectedValue(new Error("nope"));
    render(<UpstreamsView />);

    const toggle = await screen.findByLabelText("Enable app-pool");
    await user.click(toggle);

    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
    expect(toast.error).toHaveBeenCalled();
  });
});
```

- [ ] **Step 9: Run the full frontend suite**

```bash
npx vitest run && npx eslint src && npx tsc --noEmit
```
Expected: all pass. `proxy-hosts-view.tsx` still imports `UpstreamDialog` from its old path — fix that import to `@/components/upstreams/upstream-dialog` so this task compiles; Task 2 deletes the usage.

- [ ] **Step 10: Check line endings and commit**

```bash
cd /c/Projects/megoopm
git status --porcelain | awk '{print $2}' | while read f; do [ -f "$f" ] && git ls-files --eol "$f"; done | grep -v "w/lf" || echo "ALL LF"
git add frontend/src
git commit -m "feat(ui): give upstream pools their own page and nav entry"
```

## Task 2: Strip pools out of the proxy-hosts page

**Files:**
- Modify: `frontend/src/components/proxy-hosts/proxy-hosts-view.tsx`
- Modify: `frontend/src/components/proxy-hosts/proxy-hosts-view.test.tsx`

**Interfaces:**
- Consumes: `UpstreamsView` from Task 1 (pools now live there).
- Produces: a single-purpose `ProxyHostsView` with no `Tabs`.

- [ ] **Step 1: Delete the pool tests from the proxy-hosts suite**

Remove `toggles an upstream pool`, `reverts a pool toggle that fails`, the `openPools` helper, the `makePool` fixture, and the `upstreams`/`type Upstream` imports. Keep `toggles a proxy host`. `upstreams.list` is still mocked in `mount()` because `ProxyHostsView` no longer calls it — remove that spy too.

- [ ] **Step 2: Run it and watch it fail**

```bash
cd frontend && npx vitest run src/components/proxy-hosts/proxy-hosts-view.test.tsx
```
Expected: FAIL — `toggles a proxy host` still passes, but the host toggle is found via a `Tabs`-wrapped table that is about to change. If it passes, that is fine; this step's purpose is to confirm the remaining test exercises the hosts table directly.

- [ ] **Step 3: Remove the pools half from the view**

Delete: the `pools`, `poolDialog`, `deletePool` state; `upstreams.list()` from the `Promise.all` in `load` (and the `p` destructuring); `setPoolEnabled`; the pools `TabsPanel` (lines 285–367); the `UpstreamDialog` block (382–401); the delete-pool `ConfirmDeleteDialog` (403–414). Then unwrap the hosts `TabsPanel` and delete the `Tabs`/`TabsList` scaffolding entirely, promoting the hosts table to the page body.

Prune the now-unused imports: `Tabs, TabsList, TabsPanel, TabsTab`, `UpstreamDialog`, `upstreams`, `type Upstream`, `LB_METHOD_LABELS`, and `Server` if nothing else uses it. `npx eslint src` will name any you miss.

- [ ] **Step 4: Run the tests**

```bash
npx vitest run && npx eslint src && npx tsc --noEmit
```
Expected: all pass, no unused-import errors.

- [ ] **Step 5: Commit**

```bash
cd /c/Projects/megoopm && git add frontend/src && \
  git commit -m "refactor(ui): make the proxy hosts page single-purpose"
```

---

# Phase 2 — Pool context and the `backup` validation fix

## Task 3: `UpstreamContext` enum and migration

**Files:**
- Modify: `backend/app/models/enums.py`, `backend/app/models/upstream.py`
- Create: `backend/alembic/versions/0012_upstream_context.py`
- Create: `backend/tests/test_upstream_context.py`

**Interfaces:**
- Produces: `UpstreamContext` (`http` | `stream` | `both`), `Upstream.context` defaulting to `http`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_upstream_context.py`:

```python
"""Pool context: where a pool is allowed to be used (MEG stream pools)."""

from __future__ import annotations

from app.models.enums import UpstreamContext
from app.models.upstream import Upstream


def test_context_defaults_to_http() -> None:
    # Every pool that exists today backs a proxy host; http keeps them working.
    pool = Upstream(name="p")
    assert pool.context is None or pool.context == UpstreamContext.http


def test_context_values_are_stable_strings() -> None:
    # The value, not the member name, is what lands in Postgres.
    assert [c.value for c in UpstreamContext] == ["http", "stream", "both"]
```

- [ ] **Step 2: Run it and watch it fail**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --user root -v "C:/Projects/megoopm/backend:/src:ro" \
  --entrypoint sh megoopm-backend:latest -c "
  cp -r /src /work && cd /work && pip install -q --no-input 'pytest>=8.2' 'pytest-asyncio>=0.23' 'aiosqlite>=0.20'
  python -m pytest tests/test_upstream_context.py -q -p no:warnings"
```
Expected: FAIL — `ImportError: cannot import name 'UpstreamContext'`.

- [ ] **Step 3: Add the enum**

In `app/models/enums.py`, after `LoadBalanceMethod`:

```python
class UpstreamContext(enum.StrEnum):
    """Which nginx context a pool may be rendered into.

    ``upstream`` blocks are context-local: one defined in ``http {}`` is
    invisible to ``stream {}``. This says where a pool is allowed to be
    attached, which also constrains its load-balancing method — ``ip_hash``
    exists only in ``http``.
    """

    http = "http"
    stream = "stream"
    both = "both"
```

- [ ] **Step 4: Add the column**

In `app/models/upstream.py`, import `UpstreamContext` and add after `lb_method`:

```python
    context: Mapped[UpstreamContext] = mapped_column(
        Enum(
            UpstreamContext,
            name="upstream_context",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=UpstreamContext.http,
        server_default=UpstreamContext.http.value,
    )
```

- [ ] **Step 5: Write the migration**

Create `backend/alembic/versions/0012_upstream_context.py`:

```python
"""Pool context: which nginx context a pool may render into

``upstream`` blocks are context-local, so a pool has to declare whether it backs
HTTP proxy hosts, TCP/UDP streams, or both. Existing pools become ``http``,
which is what every pool in the database is today.

Revision ID: 0012_upstream_context
Revises: 0011_cluster_sweep
Create Date: 2026-08-30 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0012_upstream_context"
down_revision: str | None = "0011_cluster_sweep"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONTEXT = sa.Enum("http", "stream", "both", name="upstream_context")


def upgrade() -> None:
    _CONTEXT.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "upstreams",
        sa.Column("context", _CONTEXT, server_default="http", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("upstreams", "context")
    _CONTEXT.drop(op.get_bind(), checkfirst=True)
```

- [ ] **Step 6: Run the tests, then the migration against real Postgres**

```bash
docker run -d --name ctx-db -e POSTGRES_USER=megoopm -e POSTGRES_PASSWORD=megoopm \
  -e POSTGRES_DB=megoopm -p 55432:5432 postgres:16-alpine && sleep 8
MSYS_NO_PATHCONV=1 docker run --rm --network host -v "C:/Projects/megoopm/backend:/app" -w /app \
  -e DATABASE_URL=postgresql+asyncpg://megoopm:megoopm@127.0.0.1:55432/megoopm -e SECRET_KEY=test \
  --entrypoint sh megoopm-backend:latest -c "alembic upgrade head && alembic downgrade 0011_cluster_sweep && alembic upgrade head"
docker rm -f ctx-db
```
Expected: all three run clean. SQLite never exercises enum types, so this check is not optional.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models backend/alembic backend/tests && \
  git commit -m "feat(upstreams): add a pool context column"
```

## Task 4: Validation rules 1 and 2

**Files:**
- Modify: `backend/app/schemas/upstream.py`, `backend/app/services/upstream.py`, `backend/app/api/routes/upstreams.py`
- Modify: `backend/tests/test_upstream_context.py`

**Interfaces:**
- Consumes: `UpstreamContext` from Task 3.
- Produces: `upstream_service.InvalidPoolConfigError(message: str)`, raised for both rules; routes map it to 422.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_upstream_context.py`:

```python
import pytest
from app.models.enums import LoadBalanceMethod
from app.services import upstream as upstream_service


@pytest.mark.parametrize("context", [UpstreamContext.stream, UpstreamContext.both])
def test_ip_hash_rejected_outside_http(context: UpstreamContext) -> None:
    # ip_hash is not a stream directive; nginx -t fails hard on it there.
    with pytest.raises(upstream_service.InvalidPoolConfigError) as err:
        upstream_service.validate_pool_config(
            lb_method=LoadBalanceMethod.ip_hash, context=context, has_backup=False
        )
    assert "ip_hash" in str(err.value)


@pytest.mark.parametrize(
    "method",
    [LoadBalanceMethod.hash, LoadBalanceMethod.ip_hash, LoadBalanceMethod.random],
)
def test_backup_rejected_with_hashing_methods(method: LoadBalanceMethod) -> None:
    # nginx: "cannot be used along with the hash, ip_hash, and random methods".
    with pytest.raises(upstream_service.InvalidPoolConfigError) as err:
        upstream_service.validate_pool_config(
            lb_method=method, context=UpstreamContext.http, has_backup=True
        )
    assert "backup" in str(err.value)


def test_backup_allowed_with_round_robin_and_least_conn() -> None:
    for method in (LoadBalanceMethod.round_robin, LoadBalanceMethod.least_conn):
        upstream_service.validate_pool_config(
            lb_method=method, context=UpstreamContext.http, has_backup=True
        )
```

- [ ] **Step 2: Run and watch them fail**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --user root -v "C:/Projects/megoopm/backend:/src:ro" \
  --entrypoint sh megoopm-backend:latest -c "
  cp -r /src /work && cd /work && pip install -q --no-input 'pytest>=8.2' 'pytest-asyncio>=0.23' 'aiosqlite>=0.20'
  python -m pytest tests/test_upstream_context.py -q -p no:warnings"
```
Expected: FAIL — `AttributeError: module has no attribute 'InvalidPoolConfigError'`.

- [ ] **Step 3: Implement the validator**

In `app/services/upstream.py`, beside `UpstreamInUseError`:

```python
class InvalidPoolConfigError(Exception):
    """A pool's method, context and backends cannot be combined."""


# nginx forbids backup servers with any hashing or random method:
# "The parameter cannot be used along with the hash, ip_hash, and random
# load balancing methods."
_NO_BACKUP_METHODS = {
    LoadBalanceMethod.hash,
    LoadBalanceMethod.ip_hash,
    LoadBalanceMethod.random,
}


def validate_pool_config(
    *, lb_method: LoadBalanceMethod, context: UpstreamContext, has_backup: bool
) -> None:
    """Reject combinations nginx would refuse at ``nginx -t``.

    Catching these here matters: a config error found at ``nginx -t`` rolls back
    the entire apply for every managed object with one generic message.
    """
    if lb_method is LoadBalanceMethod.ip_hash and context is not UpstreamContext.http:
        raise InvalidPoolConfigError(
            "ip_hash is not supported for TCP/UDP streams. Use hash or least_conn."
        )
    if has_backup and lb_method in _NO_BACKUP_METHODS:
        raise InvalidPoolConfigError(
            f"nginx does not allow backup servers with the {lb_method.value} method."
        )
```

Call it from `create_upstream` and `update_upstream` with the merged post-update values, computing `has_backup` from the pool's backends.

- [ ] **Step 4: Add `context` to the schemas**

In `app/schemas/upstream.py`: add to `UpstreamBase`

```python
    context: UpstreamContext = Field(
        default=UpstreamContext.http,
        description="Where the pool may be attached: http, stream, or both",
    )
```

and to `UpstreamUpdate`: `context: UpstreamContext | None = None`.

- [ ] **Step 5: Map the error to 422 in the routes**

In `app/api/routes/upstreams.py`, on both the create and update handlers:

```python
    except upstream_service.InvalidPoolConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
```

- [ ] **Step 6: Run the tests, regenerate OpenAPI, run everything**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --user root -v "C:/Projects/megoopm/backend:/src" -w /src \
  --entrypoint sh megoopm-backend:latest -c "python -m scripts.export_openapi"
MSYS_NO_PATHCONV=1 docker run --rm --user root -v "C:/Projects/megoopm/backend:/src:ro" \
  --entrypoint sh megoopm-backend:latest -c "
  cp -r /src /work && cd /work && pip install -q --no-input 'pytest>=8.2' 'pytest-asyncio>=0.23' 'aiosqlite>=0.20' 'ruff>=0.6'
  python -m pytest -q -p no:warnings && python -m ruff check ."
```
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend && git commit -m "feat(upstreams): validate pool context and the backup/hash combination"
```

## Task 5: Rules 4 and 5 — pools may only be attached where their context allows

**Files:**
- Modify: `backend/app/services/upstream.py`, `backend/app/services/proxy_host.py`
- Modify: `backend/tests/test_upstream_context.py`

**Interfaces:**
- Consumes: `UpstreamContext`, `InvalidPoolConfigError` from Task 4.
- Produces: `upstream_service.assert_usable_in(pool: Upstream, context: UpstreamContext) -> None`; `upstream_service.reference_counts(db, upstream_id) -> dict[str, int]` returning keys `"proxy_hosts"` and `"streams"` (streams is always 0 until Task 11).

- [ ] **Step 1: Write the failing tests**

```python
from app.models.upstream import Upstream


def test_http_only_pool_rejected_for_streams() -> None:
    pool = Upstream(name="web", context=UpstreamContext.http)
    with pytest.raises(upstream_service.InvalidPoolConfigError) as err:
        upstream_service.assert_usable_in(pool, UpstreamContext.stream)
    assert "web" in str(err.value)


def test_stream_only_pool_rejected_for_proxy_hosts() -> None:
    pool = Upstream(name="db", context=UpstreamContext.stream)
    with pytest.raises(upstream_service.InvalidPoolConfigError):
        upstream_service.assert_usable_in(pool, UpstreamContext.http)


def test_both_pool_is_usable_everywhere() -> None:
    pool = Upstream(name="shared", context=UpstreamContext.both)
    upstream_service.assert_usable_in(pool, UpstreamContext.http)
    upstream_service.assert_usable_in(pool, UpstreamContext.stream)
```

- [ ] **Step 2: Run and watch them fail**

Same container command as Task 4 Step 2. Expected: FAIL — no `assert_usable_in`.

- [ ] **Step 3: Implement**

```python
def assert_usable_in(pool: Upstream, context: UpstreamContext) -> None:
    """Reject attaching a pool somewhere its context does not allow."""
    if pool.context is UpstreamContext.both or pool.context is context:
        return
    where = "streams" if context is UpstreamContext.stream else "proxy hosts"
    raise InvalidPoolConfigError(f"Pool '{pool.name}' is not available for {where}.")
```

Add `reference_counts`, counting `ProxyHost.upstream_id` and `ProxyHostLocation.upstream_id` under `"proxy_hosts"` and returning `"streams": 0` for now (Task 11 fills it in). In `update_upstream`, when `context` narrows, call it and raise:

```python
    raise InvalidPoolConfigError(
        f"Pool '{pool.name}' is used by {n} proxy host(s); keep 'http' or 'both'."
    )
```

Call `assert_usable_in(pool, UpstreamContext.http)` from `proxy_host` create/update where `upstream_id` is set, including locations.

- [ ] **Step 4: Run everything**

Same command as Task 4 Step 6. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend && git commit -m "feat(upstreams): enforce pool context when attaching and narrowing"
```

## Task 6: Split `DesiredState.upstreams`

This lands the renderer split now, with `stream_upstreams` empty, so Phase 3 is purely additive.

**Files:**
- Modify: `backend/app/services/nginx/state.py`, `loader.py`, `renderer.py`
- Modify: `backend/tests/test_nginx_render.py`, `test_meg24_render.py`, and any test constructing `DesiredState(upstreams=...)`

**Interfaces:**
- Produces: `DesiredState.http_upstreams`, `DesiredState.stream_upstreams` (both `tuple[UpstreamSpec, ...]`, defaulting empty). `DesiredState.upstreams` no longer exists.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_nginx_render.py`:

```python
def test_stream_pools_render_into_the_stream_directory() -> None:
    """A pool for a stream must not be emitted into http{} — and vice versa."""
    pool = UpstreamSpec(id=9, name="db", backends=(BackendSpec(host="10.0.0.9", port=5432),))
    state = DesiredState(stream_upstreams=(pool,))

    http_files = render_config(state)
    stream_files = render_stream_config(state)

    assert "megoopm-upstream-9.conf" not in http_files
    assert "megoopm-upstream-9.conf" in stream_files
    assert "upstream megoopm_upstream_9 {" in stream_files["megoopm-upstream-9.conf"]
```

- [ ] **Step 2: Run and watch it fail**

```bash
MSYS_NO_PATHCONV=1 docker run --rm --user root -v "C:/Projects/megoopm/backend:/src:ro" \
  --entrypoint sh megoopm-backend:latest -c "
  cp -r /src /work && cd /work && pip install -q --no-input 'pytest>=8.2' 'pytest-asyncio>=0.23' 'aiosqlite>=0.20'
  python -m pytest tests/test_nginx_render.py -q -p no:warnings"
```
Expected: FAIL — `DesiredState` has no `stream_upstreams`.

- [ ] **Step 3: Split the field**

In `state.py`, replace `upstreams` with:

```python
    # Pools referenced by proxy hosts, rendered into http{}.
    http_upstreams: tuple[UpstreamSpec, ...] = field(default_factory=tuple)
    # Pools referenced by streams, rendered into stream{}. upstream blocks are
    # context-local, so a pool used by both is emitted into both directories
    # under the same nginx name — separate namespaces, not a collision.
    stream_upstreams: tuple[UpstreamSpec, ...] = field(default_factory=tuple)
```

Update the class docstring, which currently describes a single `upstreams` field.

- [ ] **Step 4: Update the loader and renderer**

`loader.py`: rename `upstream_specs` to `http_upstream_specs` and pass it as `http_upstreams=`; pass `stream_upstreams=()` for now.

`renderer.py`: `render_config` iterates `state.http_upstreams`; `render_stream_config` gains, before the stream loop:

```python
    for upstream in state.stream_upstreams:
        files[f"megoopm-upstream-{upstream.id}.conf"] = _render_upstream(
            upstream, directives=_STREAM_LB_DIRECTIVES
        )
```

and `_render_upstream` gains a `directives` parameter defaulting to `_LB_DIRECTIVES`. Add:

```python
# ip_hash exists only in http{}. Validation should stop it reaching a stream
# pool; if a hand-edited row does, fail loudly rather than emit config that
# breaks nginx -t on every node.
_STREAM_LB_DIRECTIVES = {k: v for k, v in _LB_DIRECTIVES.items() if k != "ip_hash"}


def _stream_directive(upstream: UpstreamSpec) -> str:
    try:
        return _STREAM_LB_DIRECTIVES[upstream.lb_method]
    except KeyError:
        raise ValueError(
            f"pool {upstream.name!r} uses {upstream.lb_method}, which nginx's "
            "stream module does not support"
        ) from None
```

- [ ] **Step 5: Fix every caller**

Search and update: `grep -rn "upstreams=" backend/tests backend/app | grep -i desiredstate`.

- [ ] **Step 6: Run everything**

Full backend command from Global Constraints. Expected: PASS, ruff clean.

- [ ] **Step 7: Commit**

```bash
git add backend && git commit -m "refactor(nginx): split DesiredState pools by nginx context"
```

## Task 7: Pool dialog — context selector and filtered methods

**Files:**
- Modify: `frontend/src/components/upstreams/upstream-dialog.tsx`
- Create: `frontend/src/components/upstreams/upstream-dialog.test.tsx`

**Interfaces:**
- Consumes: `context` on the `Upstream` type (regenerated in Task 4).

- [ ] **Step 1: Write the failing tests**

```tsx
it("offers ip_hash only for HTTP-only pools", async () => {
  const user = userEvent.setup();
  render(<UpstreamDialog open onOpenChange={() => {}} upstream={makePool()} onSaved={() => {}} />);

  await user.click(screen.getByLabelText("Load balancing method"));
  expect(screen.getByRole("option", { name: /ip_hash/i })).toBeInTheDocument();

  await user.keyboard("{Escape}");
  await user.click(screen.getByLabelText("Context"));
  await user.click(screen.getByRole("option", { name: /Streams only/i }));
  await user.click(screen.getByLabelText("Load balancing method"));
  // ip_hash is not a stream directive.
  expect(screen.queryByRole("option", { name: /ip_hash/i })).not.toBeInTheDocument();
});

it("resets ip_hash when the context stops being HTTP-only", async () => {
  const user = userEvent.setup();
  render(
    <UpstreamDialog open onOpenChange={() => {}} upstream={makePool({ lb_method: "ip_hash" })} onSaved={() => {}} />,
  );
  await user.click(screen.getByLabelText("Context"));
  await user.click(screen.getByRole("option", { name: /Both/i }));
  // Saving ip_hash with a non-http context would just 422.
  expect(screen.getByLabelText("Load balancing method")).toHaveTextContent(/round.robin/i);
});
```

- [ ] **Step 2: Run and watch them fail**

```bash
cd frontend && npx vitest run src/components/upstreams/upstream-dialog.test.tsx
```
Expected: FAIL — no `Context` control.

- [ ] **Step 3: Implement**

Add a `Context` `Select` with options `HTTP only` / `Streams only` / `Both` mapping to `http` / `stream` / `both`, and a hint: *"Where this pool may be attached. Streams cannot use ip_hash."* Derive the method list:

```ts
const methods = form.context === "http" ? LB_METHODS : LB_METHODS.filter((m) => m !== "ip_hash");
```

When context changes away from `http` while `lb_method === "ip_hash"`, set `lb_method` to `round_robin` in the same state update.

- [ ] **Step 4: Run the suite**

```bash
npx vitest run && npx eslint src && npx tsc --noEmit
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /c/Projects/megoopm && git add frontend/src && \
  git commit -m "feat(ui): choose a pool's context and filter its methods to match"
```

---

# Phase 3 — Stream pools

## Task 8: Migration and model for `streams.upstream_id`

**Files:**
- Modify: `backend/app/models/stream.py`
- Create: `backend/alembic/versions/0013_stream_upstream.py`
- Create: `backend/tests/test_stream_pools.py`

**Interfaces:**
- Produces: `Stream.upstream_id: int | None`, `Stream.forward_host: str | None`, `Stream.forward_port: int | None`, check constraint `stream_target_exactly_one`.

- [ ] **Step 1: Write the failing test**

```python
"""Streams targeting an upstream pool instead of a single host:port."""

from __future__ import annotations

import pytest
from app.models.stream import Stream
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 's.db'}", future=True)
    Stream.__table__.create(engine)
    return engine


def test_rejects_both_targets(engine) -> None:
    with Session(engine) as s, pytest.raises(IntegrityError):
        s.add(Stream(incoming_port=1, forward_host="h", forward_port=2, upstream_id=1))
        s.commit()


def test_rejects_neither_target(engine) -> None:
    with Session(engine) as s, pytest.raises(IntegrityError):
        s.add(Stream(incoming_port=1))
        s.commit()


def test_accepts_a_pool_only_target(engine) -> None:
    with Session(engine) as s:
        s.add(Stream(incoming_port=1, upstream_id=5))
        s.commit()
```

- [ ] **Step 2: Run and watch it fail**

Container command as before, targeting `tests/test_stream_pools.py`. Expected: FAIL — `Stream` has no `upstream_id`.

- [ ] **Step 3: Update the model**

Make `forward_host` / `forward_port` `Mapped[str | None]` / `Mapped[int | None]` with `nullable=True`, add:

```python
    upstream_id: Mapped[int | None] = mapped_column(
        ForeignKey("upstreams.id", ondelete="RESTRICT"), nullable=True, index=True
    )
```

and replace the port constraint plus add the target constraint in `__table_args__`:

```python
        CheckConstraint(
            "forward_port IS NULL OR forward_port BETWEEN 1 AND 65535",
            name="forward_port_range",
        ),
        CheckConstraint(
            "(forward_host IS NOT NULL AND forward_port IS NOT NULL AND upstream_id IS NULL)"
            " OR (forward_host IS NULL AND forward_port IS NULL AND upstream_id IS NOT NULL)",
            name="stream_target_exactly_one",
        ),
```

- [ ] **Step 4: Write the migration**

`0013_stream_upstream.py`, `down_revision = "0012_upstream_context"`. Upgrade: `op.alter_column` both forward columns to `nullable=True`; `op.add_column` `upstream_id` with the FK and index; `op.drop_constraint("ck_streams_forward_port_range")` then `op.create_check_constraint` for both new constraints. Downgrade reverses, and must `DELETE FROM streams WHERE upstream_id IS NOT NULL` before restoring `NOT NULL` — document that in the docstring as data loss.

- [ ] **Step 5: Run tests and the migration round-trip**

Use the Postgres round-trip from Task 3 Step 6, upgrading to head and back to `0012_upstream_context`.

- [ ] **Step 6: Commit**

```bash
git add backend && git commit -m "feat(streams): allow a stream to target an upstream pool"
```

## Task 9: Stream schemas and rules 3 and 6

**Files:**
- Modify: `backend/app/schemas/stream.py`, `backend/app/services/stream.py`, `backend/app/api/routes/streams.py`
- Modify: `backend/tests/test_stream_pools.py`

**Interfaces:**
- Consumes: `assert_usable_in` from Task 5.
- Produces: `StreamBase.upstream_id: int | None`; `forward_host` / `forward_port` optional.

- [ ] **Step 1: Write the failing tests**

```python
from app.schemas.stream import StreamCreate


def test_schema_rejects_both_targets() -> None:
    with pytest.raises(ValueError, match="either a forward host"):
        StreamCreate(incoming_port=1, forward_host="h", forward_port=2, upstream_id=3)


def test_schema_rejects_neither_target() -> None:
    with pytest.raises(ValueError, match="either a forward host"):
        StreamCreate(incoming_port=1)


def test_schema_accepts_a_pool() -> None:
    assert StreamCreate(incoming_port=1, upstream_id=3).upstream_id == 3
```

- [ ] **Step 2: Run and watch them fail**

Expected: FAIL — `forward_host` is still required, so a different error is raised.

- [ ] **Step 3: Implement**

Make `forward_host` / `forward_port` `| None = Field(default=None, ...)`, add `upstream_id: int | None = None`, and extend the existing `model_validator`:

```python
        host_target = self.forward_host is not None and self.forward_port is not None
        if host_target == (self.upstream_id is not None):
            raise ValueError("Set either a forward host and port, or an upstream pool.")
```

In `services/stream.py`, when `upstream_id` is set, load the pool and call `assert_usable_in(pool, UpstreamContext.stream)`; map `InvalidPoolConfigError` to 422 in the routes as in Task 4 Step 5.

- [ ] **Step 4: Regenerate OpenAPI and run everything**

Commands from Task 4 Step 6. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend && git commit -m "feat(streams): accept a pool target and validate its context"
```

## Task 10: Loader and template render a pooled stream

**Files:**
- Modify: `backend/app/services/nginx/state.py`, `loader.py`, `templates/nginx/stream.conf.j2`
- Modify: `backend/tests/test_nginx_render.py`

**Interfaces:**
- Consumes: `DesiredState.stream_upstreams` from Task 6.
- Produces: `StreamSpec.upstream_id: int | None`, `forward_host: str | None`, `forward_port: int | None`.

- [ ] **Step 1: Write the failing test**

```python
def test_pooled_stream_proxies_to_the_pool() -> None:
    pool = UpstreamSpec(id=9, name="db", backends=(BackendSpec(host="10.0.0.9", port=5432),))
    stream = StreamSpec(id=1, incoming_port=5432, upstream_id=9, tcp_forwarding=True)
    files = render_stream_config(DesiredState(streams=(stream,), stream_upstreams=(pool,)))
    assert "proxy_pass megoopm_upstream_9;" in files["megoopm-stream-1.conf"]


def test_host_target_stream_is_unchanged() -> None:
    stream = StreamSpec(
        id=1, incoming_port=5432, forward_host="db.internal", forward_port=5432, tcp_forwarding=True
    )
    files = render_stream_config(DesiredState(streams=(stream,)))
    assert "proxy_pass db.internal:5432;" in files["megoopm-stream-1.conf"]
```

- [ ] **Step 2: Run and watch it fail**

Expected: FAIL — `StreamSpec` has no `upstream_id`.

- [ ] **Step 3: Update the spec and template**

`StreamSpec`: `forward_host: str | None = None`, `forward_port: int | None = None`, `upstream_id: int | None = None`.

`stream.conf.j2` — replace the `proxy_pass` line:

```jinja
{%- if stream.upstream_id %}
    proxy_pass {{ pool_name }};
{%- else %}
    proxy_pass {{ stream.forward_host }}:{{ stream.forward_port }};
{%- endif %}
```

and pass `pool_name=pool_name(stream.upstream_id) if stream.upstream_id else ""` from `_render_stream`. Update the header comment, which hard-codes `host:port`.

- [ ] **Step 4: Update the loader**

Load `Stream.upstream_id` into the spec. Compute `stream_upstreams` from pools referenced by included streams, filtered to enabled pools with a usable backend — reuse the helper that already does this for proxy hosts. Skip any stream whose pool is absent from that set, mirroring the proxy-host rule; a `server` block naming a non-existent `upstream` fails `nginx -t`.

- [ ] **Step 5: Run everything and commit**

```bash
git add backend && git commit -m "feat(nginx): render streams that forward to a pool"
```

## Task 11: Rule 5 counts streams, and the 409 message

**Files:**
- Modify: `backend/app/services/upstream.py`, `backend/app/api/routes/upstreams.py`
- Modify: `backend/tests/test_upstream_context.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_upstream_context_pg.py`. It needs real rows across two
tables, so it uses the rollback-per-test Postgres fixture copied from
`tests/test_certs_service_pg.py` (that file's `_pg_available` probe and
`pg_session` fixture, with the probe changed to
`SELECT context FROM upstreams LIMIT 0`):

```python
"""Pool-context narrowing guard, against real rows (skipped without Postgres)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.core.config import settings
from app.models.enums import UpstreamContext
from app.models.stream import Stream
from app.models.upstream import Upstream
from app.services import upstream as upstream_service
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

pytestmark = pytest.mark.asyncio


async def _pg_available() -> bool:
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            await conn.exec_driver_sql("SELECT context FROM upstreams LIMIT 0")
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


@pytest.fixture
async def pg_session() -> AsyncIterator[AsyncSession]:
    if not await _pg_available():
        pytest.skip("Postgres (with the 0012 migration) not available")
    engine = create_async_engine(settings.database_url)
    conn = await engine.connect()
    trans = await conn.begin()
    session = AsyncSession(bind=conn, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


async def test_cannot_narrow_context_away_from_streams(pg_session: AsyncSession) -> None:
    """Narrowing to http would stop the pool rendering into stream{}.

    The stream's server block would then name an upstream that no longer
    exists, which fails nginx -t and rolls back every node's apply.
    """
    pool = Upstream(name="db-pool", context=UpstreamContext.both)
    pg_session.add(pool)
    await pg_session.flush()
    pg_session.add(Stream(incoming_port=15432, upstream_id=pool.id))
    await pg_session.flush()

    with pytest.raises(upstream_service.InvalidPoolConfigError, match="stream"):
        await upstream_service.update_upstream(
            pg_session, pool.id, context=UpstreamContext.http
        )


async def test_narrowing_is_allowed_once_nothing_references_it(
    pg_session: AsyncSession,
) -> None:
    pool = Upstream(name="unused-pool", context=UpstreamContext.both)
    pg_session.add(pool)
    await pg_session.flush()

    updated = await upstream_service.update_upstream(
        pg_session, pool.id, context=UpstreamContext.http
    )
    assert updated.context is UpstreamContext.http
```

- [ ] **Step 2: Run and watch it fail**

Expected: FAIL — `reference_counts` returns `"streams": 0`.

- [ ] **Step 3: Implement**

Make `reference_counts` count `Stream.upstream_id` for the `"streams"` key, and have the narrowing guard name whichever side blocks it.

- [ ] **Step 4: Fix the 409 text**

`routes/upstreams.py` currently says *"Upstream is still referenced by one or more proxy hosts"*. Change to *"Upstream is still referenced by one or more proxy hosts or streams"*, and update `UpstreamInUseError`'s docstring, which says "referenced by a proxy host".

- [ ] **Step 5: Run everything and commit**

```bash
git add backend && git commit -m "fix(upstreams): count streams when guarding pool context and deletion"
```

## Task 12: Stream dialog target mode

**Files:**
- Modify: `frontend/src/components/streams/stream-dialog.tsx`, `stream-dialog.test.tsx`

**Interfaces:**
- Consumes: `upstream_id` on the `Stream` type (regenerated in Task 9).

- [ ] **Step 1: Write the failing tests**

```tsx
it("switches between a single host and a pool", async () => {
  const user = userEvent.setup();
  renderDialog();
  expect(screen.getByLabelText("Forward host")).toBeInTheDocument();

  await user.click(screen.getByRole("radio", { name: "Pool" }));
  expect(screen.queryByLabelText("Forward host")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Upstream pool")).toBeInTheDocument();
});

it("keeps typed host values when toggling back from Pool", async () => {
  const user = userEvent.setup();
  renderDialog();
  await user.clear(screen.getByLabelText("Forward host"));
  await user.type(screen.getByLabelText("Forward host"), "cache.internal");
  await user.click(screen.getByRole("radio", { name: "Pool" }));
  await user.click(screen.getByRole("radio", { name: "Single host" }));
  expect(screen.getByLabelText("Forward host")).toHaveValue("cache.internal");
});

it("sends exactly one target", async () => {
  const user = userEvent.setup();
  renderDialog(makeStream({ upstream_id: 4, forward_host: null, forward_port: null }));
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(streams.update).toHaveBeenCalledTimes(1));
  const body = vi.mocked(streams.update).mock.calls[0][1];
  expect(body.upstream_id).toBe(4);
  expect(body.forward_host).toBeNull();
});
```

- [ ] **Step 2: Run and watch them fail**

```bash
cd frontend && npx vitest run src/components/streams/stream-dialog.test.tsx
```
Expected: FAIL — no target-mode radios.

- [ ] **Step 3: Implement**

On the Details tab, add a two-option radio group (*Single host* / *Pool*) above the target fields. Keep both `forwardHost`/`forwardPort` and `upstreamId` in form state at all times so switching does not lose typed input; send only the active mode's values, nulling the other. The pool picker lists pools where `context` is `stream` or `both`, showing the backend count. Add the `certificates`-style loading of pools to the dialog's props — the view passes them in, as it already does for certificates.

Extend the validation guards: in Pool mode, require a selected pool; in Single host mode, keep the existing port and host checks. Both call `fail(...)` so the Details tab is selected.

- [ ] **Step 4: Run the suite and commit**

```bash
npx vitest run && npx eslint src && npx tsc --noEmit
cd /c/Projects/megoopm && git add frontend/src && \
  git commit -m "feat(ui): let a stream forward to an upstream pool"
```

## Task 13: Documentation

**Files:**
- Modify: `docs/nginx-engine.md`, `docs/data-model.md`

- [ ] **Step 1: Update the docs**

`docs/nginx-engine.md` describes what renders where — add that pools render into either or both context directories, and that `ip_hash` is http-only. `docs/data-model.md` — add `upstreams.context` and the stream target constraint.

- [ ] **Step 2: Commit**

```bash
git add docs && git commit -m "docs: pool contexts and stream pool targets"
```

---

## Self-review notes

- **Spec coverage:** data model → Tasks 3, 8; validation rules 1–6 → Tasks 4, 5, 9, 11; renderer/loader → Tasks 6, 10; API → Tasks 4, 9; UI → Tasks 1, 2, 7, 12; testing folded into every task; phasing → the three phase headings.
- **Type consistency:** `UpstreamContext`, `InvalidPoolConfigError`, `validate_pool_config`, `assert_usable_in`, `reference_counts`, `http_upstreams`, `stream_upstreams`, `_STREAM_LB_DIRECTIVES` are each defined in one task and referenced by name in later ones.
- **Known thin spot:** Task 12 Step 3 describes the pool picker's props and behaviour rather than showing full JSX, because the surrounding dialog shifts when Task 9 regenerates the `Stream` type. The tests in that step pin the behaviour precisely, so the implementer has an exact target.
- **Fixed during review:** Task 11's test was written as an ellipsis with a note to "write it against the async session fixture" — a placeholder. It now carries the complete Postgres fixture and both tests.
