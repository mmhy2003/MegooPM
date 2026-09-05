# Role-Based Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the `member` role a meaning — read every page except Settings and Users, write nothing — and prove the boundary with a test that covers every route in the app.

**Architecture:** A declared authorization matrix, checked against the app's real dependency tree, is built first and seeded with the *current* state so it starts green. Every later task flips specific entries in that table and the route guards together, so a missed edit fails the suite instead of shipping. The frontend then stops offering controls the API will refuse.

**Tech Stack:** FastAPI dependencies (`CurrentUser`, `AdminUser`), pytest, Next.js 16 / React 19, vitest.

**Spec:** `docs/superpowers/specs/2026-09-05-role-based-access-design.md`

## Global Constraints

- **Backend tests cannot run on Windows** (`app/services/cluster/locks.py` imports `fcntl`). Run them in the throwaway container:
  ```bash
  export MSYS_NO_PATHCONV=1
  docker run -d --name megoopm-test --user root -v "C:/Projects/megoopm/backend:/src" -w /src \
    -e CELERY_TASK_ALWAYS_EAGER=true -e CELERY_RESULT_BACKEND=cache+memory:// \
    --entrypoint sleep megoopm-backend infinity
  docker exec megoopm-test pip install -q "pytest>=8.2" "pytest-asyncio>=0.23" "aiosqlite>=0.20" \
    "ruff>=0.6" maxminddb "webauthn>=3.0" "cbor2>=5.6"
  ```
  Run pytest **without** `-q` (`pyproject.toml` already sets it; `-qq` hides the summary).
- **Frontend formatting is not uniform.** Some files are wrapped at 100 columns, some at 80. Before running prettier on a file, check which it already satisfies (`npx prettier --check --print-width 100 <file>`); if it satisfies neither, hand-match the surrounding style. Always read `git diff --stat` before committing — a one-line change reporting 50 changed lines is formatting churn.
- **A member writes nothing** except their own `/users/me/*` routes. No creates, edits, deletes, toggles, nginx reload, certificate renewal or Ask AI.
- **Settings and Users stay admin**, API and pages both.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  ```

---

### Task 1: The authorization matrix

The backbone. Nothing else in this plan is safe without it.

**Files:**
- Create: `backend/tests/test_route_authorization.py`

**Interfaces:**
- Produces: `route_guard(route) -> str` returning `"public" | "member" | "admin"`, and `iter_routes(router, prefix="")` yielding `(method, path, route)` triples in that order. Later tasks edit only the `ROUTE_ROLES` table in this file.

- [ ] **Step 1: Write the walker and the failing test**

This FastAPI version (0.141.1) keeps included routers as `_IncludedRouter`
wrappers rather than flattening them into `app.routes`, so a naive
`for r in app.routes` finds six entries and no endpoints. Reach the real ones
through `original_router`.

Create `backend/tests/test_route_authorization.py`:

```python
"""Every route the app exposes, and the role it demands.

The guards themselves are one-line dependencies scattered over twenty route
modules, so "did we remember?" is not a question care can answer. This asks it
of the running app instead: it resolves each route's real dependency tree and
compares it against the table below. A new endpoint that is not in the table
fails here, which is the point — declaring its role is part of adding it.
"""

from __future__ import annotations

import pytest
from app.main import app


def iter_routes(router, prefix: str = ""):
    """Every endpoint under ``router``, including those behind included routers.

    FastAPI 0.141 wraps an included router in ``_IncludedRouter`` instead of
    copying its routes up, so recursion has to go through ``original_router``.
    """
    for route in getattr(router, "routes", []):
        if type(route).__name__ == "_IncludedRouter":
            context = getattr(route, "include_context", None)
            inner_prefix = prefix + (context.prefix if context else "")
            yield from iter_routes(route.original_router, inner_prefix)
        elif getattr(route, "dependant", None) is not None:
            for method in sorted(set(route.methods or ()) - {"HEAD", "OPTIONS"}):
                yield method, prefix + route.path, route
        elif getattr(route, "routes", None):
            yield from iter_routes(route, prefix)


def route_guard(route) -> str:
    """The strongest guard the route actually enforces."""
    seen: list[str] = []

    def visit(dependant) -> None:
        for sub in dependant.dependencies:
            if sub.call is not None:
                seen.append(getattr(sub.call, "__name__", ""))
            visit(sub)

    visit(route.dependant)
    if "require_admin" in seen:
        return "admin"
    if "get_current_user" in seen:
        return "member"
    return "public"


#: The role each route demands. Seeded from the app as it stood on 2026-09-05
#: and edited deliberately from there — see the task that changes each one.
ROUTE_ROLES: dict[tuple[str, str], str] = {
    # PASTE THE GENERATED TABLE HERE (see Step 2)
}


def test_every_route_declares_a_role() -> None:
    """An endpoint nobody classified is an endpoint nobody secured."""
    undeclared = [
        (method, path)
        for method, path, _ in iter_routes(app)
        if (method, path) not in ROUTE_ROLES
    ]
    assert undeclared == [], (
        "These routes are not in ROUTE_ROLES. Add each one with the role it "
        "should demand, then make the guard match."
    )


def test_no_route_is_more_open_than_declared() -> None:
    wrong = {
        (method, path): (route_guard(route), ROUTE_ROLES[(method, path)])
        for method, path, route in iter_routes(app)
        if (method, path) in ROUTE_ROLES
        and route_guard(route) != ROUTE_ROLES[(method, path)]
    }
    assert wrong == {}, "actual != declared, as (actual, declared)"


def test_the_table_has_no_routes_the_app_lost() -> None:
    """A stale entry hides the fact that an endpoint was removed or renamed."""
    live = {(method, path) for method, path, _ in iter_routes(app)}
    assert set(ROUTE_ROLES) - live == set()


@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE"])
def test_writes_are_admin_only(method: str) -> None:
    """The rule the whole feature rests on, stated once.

    The exceptions are deliberate and small: `/auth/*` is how you sign in, and
    `/users/me/*` is a member acting on their own row.
    """
    offenders = [
        path
        for (verb, path), role in ROUTE_ROLES.items()
        if verb == method
        and role != "admin"
        and not path.startswith("/api/v1/auth/")
        and not path.startswith("/api/v1/users/me")
    ]
    assert offenders == []
```

- [ ] **Step 2: Seed the table from the running app**

The table must start as a record of what is true today, so the test is green
before any guard changes and every later diff is a deliberate edit.

Write `backend/_seed_roles.py`:

```python
"""One-off: print ROUTE_ROLES entries for the app as it stands."""

from app.main import app
from tests.test_route_authorization import iter_routes, route_guard

rows = sorted((path, method, route_guard(r)) for method, path, r in iter_routes(app))
for path, method, role in rows:
    print(f'    ("{method}", "{path}"): "{role}",')
```

Run it and paste the output over the `# PASTE THE GENERATED TABLE HERE` line:

```bash
docker exec megoopm-test python _seed_roles.py
```

Then delete the helper — it is scaffolding, not part of the suite:

```bash
rm backend/_seed_roles.py
```

- [ ] **Step 3: Run the tests**

Run: `docker exec megoopm-test python -m pytest tests/test_route_authorization.py -p no:cacheprovider -p no:warnings`

Expected: `test_every_route_declares_a_role`, `test_no_route_is_more_open_than_declared` and `test_the_table_has_no_routes_the_app_lost` PASS (122 routes declared), and **`test_writes_are_admin_only` FAILS** naming `POST /api/v1/tasks/sample`. That failure is the first real finding, and Task 2 fixes it.

- [ ] **Step 4: Prove the matrix can catch a regression**

Temporarily weaken one guard and confirm the suite objects:

```bash
docker exec megoopm-test python - <<'EOF'
import io
p = "app/api/routes/proxy_hosts.py"
s = io.open(p, encoding="utf-8").read()
io.open(p + ".keep", "w", encoding="utf-8", newline="\n").write(s)
io.open(p, "w", encoding="utf-8", newline="\n").write(s.replace("_admin: AdminUser", "_user: CurrentUser", 1))
EOF
docker exec megoopm-test python -m pytest tests/test_route_authorization.py -p no:cacheprovider -p no:warnings
```

Expected: `test_no_route_is_more_open_than_declared` FAILS naming the weakened route. Restore it:

```bash
docker exec megoopm-test sh -c 'mv app/api/routes/proxy_hosts.py.keep app/api/routes/proxy_hosts.py'
docker exec megoopm-test python -m pytest tests/test_route_authorization.py -p no:cacheprovider -p no:warnings
```

Expected: back to three passing, one failing (`test_writes_are_admin_only`).

- [ ] **Step 5: Commit**

Mark the known failure with `xfail` so the tree stays green between tasks, and
remove the marker in Task 2. Add above `test_writes_are_admin_only`:

```python
@pytest.mark.xfail(reason="POST /api/v1/tasks/sample is unauthenticated; fixed next", strict=True)
```

```bash
git add backend/tests/test_route_authorization.py
git commit -m "test(api): declare the role every route demands

Twenty route modules carry one-line guards, so whether one was missed is
not a question care can answer. This asks the running app: it resolves
each route's real dependency tree and compares it against a declared
table. A new endpoint that is not in the table fails here.

Seeded from the app as it stands, so it starts as a record of the truth
and every later diff is a deliberate edit. It already finds one:
POST /api/v1/tasks/sample takes no authentication at all.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: The unauthenticated task routes

**Files:**
- Modify: `backend/app/api/routes/tasks.py`
- Modify: `backend/tests/test_route_authorization.py` (two table entries, drop the `xfail`)
- Test: `backend/tests/test_tasks_auth.py` (create)

**Interfaces:**
- Consumes: `ROUTE_ROLES` from Task 1.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_tasks_auth.py`:

```python
"""The task routes were mounted with no dependency at all.

Anyone who could reach the API could enqueue work and read task results
without signing in. Status is a member read because every page polls it after
a config write; enqueuing is an admin action because it starts work.
"""

from __future__ import annotations

from httpx import AsyncClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_a_stranger_cannot_read_a_task(db_client: AsyncClient) -> None:
    resp = await db_client.get("/api/v1/tasks/some-task-id")
    assert resp.status_code == 401


async def test_a_stranger_cannot_enqueue_work(db_client: AsyncClient) -> None:
    resp = await db_client.post("/api/v1/tasks/sample", json={"x": 1, "y": 2})
    assert resp.status_code == 401


async def test_a_member_may_read_a_task(db_client: AsyncClient, member_token: str) -> None:
    # Pages poll this after a config write; the id is unguessable and the
    # payload is a status, so reading one is not an admin act.
    resp = await db_client.get("/api/v1/tasks/some-task-id", headers=_auth(member_token))
    assert resp.status_code != 401
    assert resp.status_code != 403


async def test_a_member_cannot_enqueue_work(db_client: AsyncClient, member_token: str) -> None:
    resp = await db_client.post(
        "/api/v1/tasks/sample", headers=_auth(member_token), json={"x": 1, "y": 2}
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_tasks_auth.py -p no:cacheprovider -p no:warnings`

Expected: the two "stranger" tests FAIL (they get 200/202 instead of 401) and `test_a_member_cannot_enqueue_work` FAILS (202 instead of 403).

- [ ] **Step 3: Add the guards**

In `backend/app/api/routes/tasks.py`, add the import and the two dependencies:

```python
from app.api.deps import AdminUser, CurrentUser
```

```python
@router.post(
    "/tasks/sample",
    response_model=TaskEnqueued,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_sample(payload: SampleTaskRequest, _admin: AdminUser) -> TaskEnqueued:
    """Enqueue the sample ``add`` task; returns a task id to poll."""
    return enqueue_sample_add(payload.x, payload.y)


@router.get("/tasks/{task_id}", response_model=TaskStatus)
async def task_status(task_id: str, _user: CurrentUser) -> TaskStatus:
    """Return the status (and result, once ready) of a background task."""
    return get_task_status(task_id)
```

- [ ] **Step 4: Update the matrix and drop the xfail**

In `backend/tests/test_route_authorization.py`, change the two entries:

```python
    ("POST", "/api/v1/tasks/sample"): "admin",
    ("GET", "/api/v1/tasks/{task_id}"): "member",
```

and delete the `@pytest.mark.xfail(...)` line above `test_writes_are_admin_only`.

- [ ] **Step 5: Run both files**

Run: `docker exec megoopm-test python -m pytest tests/test_tasks_auth.py tests/test_route_authorization.py -p no:cacheprovider -p no:warnings`

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/tasks.py backend/tests/test_tasks_auth.py backend/tests/test_route_authorization.py
git commit -m "fix(api): the task routes require a signed-in user

Both were mounted with no dependency, so anyone who could reach the API
could enqueue work and read task results without signing in. Status is a
member read because every page polls it after a config write; enqueuing
is an admin act because it starts work.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Members may read the infrastructure pages

**Files:**
- Modify: `backend/app/api/routes/proxy_hosts.py`, `upstreams.py`, `certificates.py`, `access_lists.py`, `streams.py`, `redirection_hosts.py`, `dead_hosts.py`, `custom_pages.py`
- Modify: `backend/tests/test_route_authorization.py` (the GET entries for those routers)
- Test: `backend/tests/test_member_access.py` (create)

**Interfaces:**
- Consumes: `ROUTE_ROLES` from Task 1.
- Produces: `backend/tests/test_member_access.py`, extended by Task 4.

- [ ] **Step 1: Write the failing test**

`/api/v1/custom-pages` is used for the behavioural test because it needs no
PostgreSQL `ARRAY` column and so works against the SQLite `db_client` fixture.
The other routers are covered by the matrix test, which inspects the real
dependency rather than making a request.

Create `backend/tests/test_member_access.py`:

```python
"""A member reviews the instance and changes nothing.

The matrix test proves the guard on all 122 routes by inspection; these prove
the shape of the rule end to end over HTTP, so a dependency that resolves but
does not actually refuse would still be caught.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_a_member_may_list_custom_pages(
    db_client: AsyncClient, member_token: str
) -> None:
    resp = await db_client.get("/api/v1/custom-pages", headers=_auth(member_token))
    assert resp.status_code == 200


async def test_a_member_may_read_one_custom_page(
    db_client: AsyncClient, admin_token: str, member_token: str
) -> None:
    created = await db_client.post(
        "/api/v1/custom-pages",
        headers=_auth(admin_token),
        json={"name": "Notice", "description": "", "html": "<h1>hi</h1>"},
    )
    assert created.status_code == 201, created.text
    page_id = created.json()["id"]

    resp = await db_client.get(f"/api/v1/custom-pages/{page_id}", headers=_auth(member_token))

    assert resp.status_code == 200
    assert resp.json()["html"] == "<h1>hi</h1>"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/v1/custom-pages", {"name": "x", "description": "", "html": "<p>x</p>"}),
        ("post", "/api/v1/custom-pages/assist", {"instruction": "make it blue", "html": "<p>x</p>"}),
    ],
)
async def test_a_member_cannot_write(
    db_client: AsyncClient, member_token: str, method: str, path: str, body: dict
) -> None:
    resp = await getattr(db_client, method)(path, headers=_auth(member_token), json=body)
    assert resp.status_code == 403


async def test_a_member_cannot_read_the_settings(
    db_client: AsyncClient, member_token: str
) -> None:
    # Out of scope by decision: Settings stays admin, API and page both.
    resp = await db_client.get("/api/v1/settings", headers=_auth(member_token))
    assert resp.status_code == 403


async def test_a_member_cannot_list_users(db_client: AsyncClient, member_token: str) -> None:
    resp = await db_client.get("/api/v1/users", headers=_auth(member_token))
    assert resp.status_code == 403


async def test_a_member_may_still_read_their_own_account(
    db_client: AsyncClient, member_token: str
) -> None:
    """The one place a member writes is their own row; reading it must work."""
    resp = await db_client.get("/api/v1/users/me", headers=_auth(member_token))
    assert resp.status_code == 200
    assert resp.json()["role"] == "member"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_member_access.py -p no:cacheprovider -p no:warnings`

Expected: the two custom-page read tests FAIL with 403; the rest PASS already.

- [ ] **Step 3: Change the read guards**

In each of the eight modules, the GET handlers take `_admin: AdminUser` (or
`admin: AdminUser`). Change **only the GET handlers** to `_user: CurrentUser`,
leaving every POST / PATCH / PUT / DELETE untouched. Add `CurrentUser` to the
existing `from app.api.deps import ...` line in each file, and drop `AdminUser`
from that import only if no write handler in the file still uses it (none of
these eight qualify — all keep writes).

Example, `backend/app/api/routes/proxy_hosts.py`:

```python
@router.get("", response_model=list[ProxyHostRead])
async def list_proxy_hosts(_user: CurrentUser, db: SessionDep) -> list[ProxyHostRead]:
```

Apply the same change to the GET handlers in:

| File | GET routes |
| --- | --- |
| `proxy_hosts.py` | `""`, `"/{host_id}"` |
| `upstreams.py` | `""`, `"/{upstream_id}"` |
| `certificates.py` | `""`, `"/{cert_id}"` |
| `access_lists.py` | `""`, `"/{access_list_id}"` |
| `streams.py` | `""`, `"/{stream_id}"` |
| `redirection_hosts.py` | `""`, `"/{host_id}"` |
| `dead_hosts.py` | `""`, `"/{host_id}"` |
| `custom_pages.py` | `""`, `"/{page_id}"` |

- [ ] **Step 4: Update the matrix**

In `ROUTE_ROLES`, change those sixteen GET entries from `"admin"` to
`"member"`. The matrix test tells you the exact set if you miss any — run it
and read the `actual != declared` mapping.

- [ ] **Step 5: Run the tests**

Run: `docker exec megoopm-test python -m pytest tests/test_member_access.py tests/test_route_authorization.py -p no:cacheprovider -p no:warnings`

Expected: all PASS.

- [ ] **Step 6: Run the full backend suite**

Run: `docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings`

Expected: all pass. Existing per-router tests use `admin_token`, so widening a
read cannot break them.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/routes backend/tests/test_member_access.py backend/tests/test_route_authorization.py
git commit -m "feat(api): members may read the infrastructure pages

Every read was admin-only, so a member signed in, saw a full sidebar and
got a 403 from each page behind it. The eight resource routers now let
any signed-in user read; every write stays admin.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Members may read the Dashboard and Security pages

**Files:**
- Modify: `backend/app/api/routes/dashboard.py`, `crowdsec.py`
- Modify: `backend/tests/test_route_authorization.py`
- Modify: `backend/tests/test_member_access.py`

**Interfaces:**
- Consumes: `test_member_access.py` from Task 3.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_member_access.py`:

```python
async def test_a_member_may_read_the_dashboard(
    db_client: AsyncClient, member_token: str
) -> None:
    """The page the role exists to show. It was admin-only like all the rest."""
    resp = await db_client.get("/api/v1/dashboard/summary", headers=_auth(member_token))
    assert resp.status_code == 200


async def test_a_member_cannot_change_crowdsec(
    db_client: AsyncClient, member_token: str
) -> None:
    resp = await db_client.post(
        "/api/v1/crowdsec/whitelists",
        headers=_auth(member_token),
        json={"name": "office", "kind": "ip", "value": "10.0.0.1", "reason": ""},
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_member_access.py -k dashboard -p no:cacheprovider -p no:warnings`

Expected: FAIL with 403.

- [ ] **Step 3: Change the read guards**

`backend/app/api/routes/dashboard.py` — all three GETs (`/dashboard/summary`,
`/dashboard/threats`, `/dashboard/visitors`) take `_user: CurrentUser`.

`backend/app/api/routes/crowdsec.py` — these six GETs take `_user: CurrentUser`:
`/crowdsec/alerts`, `/crowdsec/decisions`, `/crowdsec/health`,
`/crowdsec/maintenance`, `/crowdsec/whitelists`, `/crowdsec/whitelists/status`.
Every other crowdsec route — bans, unbans, whitelist writes, hub updates,
CAPI — stays `AdminUser`.

- [ ] **Step 4: Update the matrix**

Change those nine GET entries in `ROUTE_ROLES` to `"member"`.

- [ ] **Step 5: Run the suites**

Run: `docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings`

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes backend/tests
git commit -m "feat(api): members may read the dashboard and the security page

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The UI stops offering what the API will refuse

**Files:**
- Create: `frontend/src/lib/auth/can-write.ts`
- Create: `frontend/src/lib/auth/can-write.test.tsx`
- Modify: `frontend/src/components/proxy-hosts/proxy-hosts-view.tsx`, `upstreams/upstreams-view.tsx`, `certificates/certificates-view.tsx`, `access-lists/access-lists-view.tsx`, `streams/streams-view.tsx`, `redirection-hosts/redirection-hosts-view.tsx`, `dead-hosts/dead-hosts-view.tsx`, `custom-pages/custom-pages-view.tsx`, `security/security-view.tsx`
- Test: `frontend/src/components/proxy-hosts/proxy-hosts-view.test.tsx`

**Interfaces:**
- Produces: `useCanWrite(): boolean` from `@/lib/auth/can-write`.

- [ ] **Step 1: Write the failing test for the hook**

Create `frontend/src/lib/auth/can-write.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

import { useCanWrite } from "@/lib/auth/can-write";

const useAuth = vi.hoisted(() => vi.fn());
vi.mock("@/lib/auth/context", () => ({ useAuth }));

describe("useCanWrite", () => {
  it("is true for an admin", () => {
    useAuth.mockReturnValue({ user: { role: "admin" } });
    expect(renderHook(() => useCanWrite()).result.current).toBe(true);
  });

  it("is false for a member", () => {
    useAuth.mockReturnValue({ user: { role: "member" } });
    expect(renderHook(() => useCanWrite()).result.current).toBe(false);
  });

  it("is false while the user is still unknown", () => {
    // Defaulting to true would flash write controls on every page load and
    // let a member click one before the answer arrives.
    useAuth.mockReturnValue({ user: null });
    expect(renderHook(() => useCanWrite()).result.current).toBe(false);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/lib/auth/can-write.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the hook**

Create `frontend/src/lib/auth/can-write.ts`:

```ts
"use client";

import { useAuth } from "@/lib/auth/context";

/**
 * Whether the signed-in user may change anything.
 *
 * A convenience over the API, never a substitute for it: every write is
 * refused by `require_admin` regardless of what the UI renders. This exists so
 * a member is not offered buttons that will 403.
 *
 * False while the user is unknown. Defaulting to true would flash write
 * controls on every page load and let a member click one before the answer
 * arrives.
 */
export function useCanWrite(): boolean {
  return useAuth().user?.role === "admin";
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `cd frontend && npx vitest run src/lib/auth/can-write.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Write the failing test for a list page**

Add to `frontend/src/components/proxy-hosts/proxy-hosts-view.test.tsx`, inside
the existing top-level `describe`:

```tsx
  it("offers no write controls to a member", async () => {
    // Hidden, not disabled: a disabled button invites a click and explains
    // nothing, and the API would refuse it anyway.
    vi.mocked(useAuth).mockReturnValue({ user: { role: "member" } } as never);
    render(<ProxyHostsView />);
    await screen.findByRole("searchbox", { name: "Search proxy hosts" });

    expect(screen.queryByRole("button", { name: /New proxy host/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Edit / })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Delete / })).not.toBeInTheDocument();
    expect(screen.getByText(/read-only/i)).toBeInTheDocument();
  });

  it("offers them to an admin", async () => {
    vi.mocked(useAuth).mockReturnValue({ user: { role: "admin" } } as never);
    render(<ProxyHostsView />);
    expect(await screen.findByRole("button", { name: /New proxy host/i })).toBeInTheDocument();
  });
```

At the top of that test file, mock the auth context and default it to admin so
every existing test in the file keeps its current behaviour:

```tsx
const useAuth = vi.hoisted(() => vi.fn(() => ({ user: { role: "admin" } })));
vi.mock("@/lib/auth/context", () => ({ useAuth }));
```

`vi.hoisted` is required: a `vi.mock` factory is hoisted above the imports, so
a factory closing over a plain top-level `const` fails at collection with
"Cannot access before initialization".

- [ ] **Step 6: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/components/proxy-hosts/proxy-hosts-view.test.tsx`
Expected: the member test FAILS — the New button is present and there is no read-only note.

- [ ] **Step 7: Hide the controls**

In `proxy-hosts-view.tsx`, read the hook once and gate the three control sites.

```tsx
import { useCanWrite } from "@/lib/auth/can-write";
```

```tsx
  const canWrite = useCanWrite();
```

Wrap the create button:

```tsx
      {canWrite ? (
        <Button size="sm" onClick={() => setDialog({ open: true })}>
          <Plus /> New proxy host
        </Button>
      ) : (
        <p className="text-muted-foreground text-sm">Read-only — ask an admin to make changes.</p>
      )}
```

Wrap the row actions cell contents:

```tsx
                    <TableCell>
                      <div className="flex justify-end gap-1">
                        {canWrite ? (
                          <>
                            {/* the existing Edit and Delete buttons, unchanged */}
                          </>
                        ) : null}
                      </div>
                    </TableCell>
```

And pass it to the enable toggle, which is a write:

```tsx
                        <EnabledToggle
                          checked={host.enabled}
                          name={host.domain_names[0]}
                          onToggle={(next) => setEnabled(host, next)}
                          disabled={!canWrite}
                        />
```

Check `frontend/src/components/hosts/enabled-toggle.tsx` for an existing
`disabled` prop; if it has none, add one and forward it to the underlying
switch. Note a disabled base-ui switch renders as a `span` with
`aria-disabled`, not a `button`, so a test must not query it by role.

- [ ] **Step 8: Run it to verify it passes**

Run: `cd frontend && npx vitest run src/components/proxy-hosts`
Expected: PASS.

- [ ] **Step 9: Apply the same pattern to the other eight views**

Repeat Step 7 in `upstreams-view.tsx`, `certificates-view.tsx`,
`access-lists-view.tsx`, `streams-view.tsx`, `redirection-hosts-view.tsx`,
`dead-hosts-view.tsx`, `custom-pages-view.tsx` and `security-view.tsx`, using
each page's own create-button label and row actions. In `custom-pages-view.tsx`
leave the row's **Preview** affordance alone — previewing is a read. In
`security-view.tsx` the write controls are the ban / unban / whitelist buttons;
the tables themselves stay.

Do **not** touch `users-view.tsx` or `dns-credentials-view.tsx`: those pages are
admin-only in full, so a member never reaches them.

- [ ] **Step 10: Run the frontend suite, typecheck and lint**

```bash
cd frontend && npx vitest run && npx tsc --noEmit && npm run lint
```
Expected: all pass.

- [ ] **Step 11: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): a member is not offered controls the API will refuse

Hidden rather than disabled: a disabled button invites a click and
explains nothing. A short read-only note takes their place so the
absence reads as intentional rather than broken.

The hook is a convenience over the API, never a substitute: every write
is still refused by require_admin regardless of what renders.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Settings and Users are not reachable by URL

**Files:**
- Create: `frontend/src/components/admin-only.tsx`
- Create: `frontend/src/components/admin-only.test.tsx`
- Modify: `frontend/src/app/(app)/settings/page.tsx`, `frontend/src/app/(app)/users/page.tsx`

**Interfaces:**
- Consumes: `useCanWrite` is *not* used here — this gates on role directly, because a page may be admin-only without being a write.
- Produces: `<AdminOnly>{children}</AdminOnly>`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/admin-only.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { AdminOnly } from "@/components/admin-only";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const useAuth = vi.hoisted(() => vi.fn());
vi.mock("@/lib/auth/context", () => ({ useAuth }));

describe("AdminOnly", () => {
  it("renders the page for an admin", () => {
    useAuth.mockReturnValue({ user: { role: "admin" } });
    render(<AdminOnly>secret</AdminOnly>);
    expect(screen.getByText("secret")).toBeInTheDocument();
  });

  it("sends a member back to the dashboard", async () => {
    // The nav does not link here, but typing the URL used to render a page
    // whose every request then failed.
    useAuth.mockReturnValue({ user: { role: "member" } });
    render(<AdminOnly>secret</AdminOnly>);
    await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
    expect(screen.queryByText("secret")).not.toBeInTheDocument();
  });

  it("shows nothing while the role is still unknown", () => {
    useAuth.mockReturnValue({ user: null });
    cleanup();
    render(<AdminOnly>secret</AdminOnly>);
    expect(screen.queryByText("secret")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npx vitest run src/components/admin-only.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the component**

Create `frontend/src/components/admin-only.tsx`:

```tsx
"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "@/lib/auth/context";

/**
 * Renders its children only for an admin, and sends anyone else home.
 *
 * The sidebar already omits these pages for a member, but typing the URL
 * rendered a page whose every request then failed, which reads as a broken
 * app rather than a closed door. The API remains the enforcement point; this
 * only decides what is worth rendering.
 */
export function AdminOnly({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const router = useRouter();
  const allowed = user?.role === "admin";

  useEffect(() => {
    if (user && !allowed) router.push("/");
  }, [user, allowed, router]);

  // Nothing until the role is known: rendering first would fire the page's
  // admin-only requests on a member's behalf and fill the console with 403s.
  return allowed ? <>{children}</> : null;
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `cd frontend && npx vitest run src/components/admin-only.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Wrap the two pages**

In `frontend/src/app/(app)/settings/page.tsx` and
`frontend/src/app/(app)/users/page.tsx`, wrap the existing view:

```tsx
import { AdminOnly } from "@/components/admin-only";

export default function Page() {
  return (
    <AdminOnly>
      <SettingsView />
    </AdminOnly>
  );
}
```

Keep each file's existing metadata export and view import as they are; only the
returned element changes.

- [ ] **Step 6: Run the frontend suite, typecheck and lint**

```bash
cd frontend && npx vitest run && npx tsc --noEmit && npm run lint
```
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): Settings and Users are closed to members by URL too

The sidebar already omitted them, but typing the URL rendered a page
whose every request then failed — a broken app rather than a closed
door.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Document the roles, and tear down

**Files:**
- Modify: `docs/` — the file describing users and authentication (find it with `grep -rln "admin\b.*member\|UserRole" docs/*.md`); if none exists, create `docs/roles.md`

- [ ] **Step 1: Write the documentation**

Add a section stating: the two roles; that a member reads every page except
Settings and Users and writes nothing but their own account; that the API is
the enforcement point and the UI only decides what to offer; and that
`backend/tests/test_route_authorization.py` holds the authoritative table, so
a new endpoint must declare its role there or the suite fails.

Include the verification an operator can run:

```bash
# Sign in as a member, then:
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" \
  https://<host>/api/v1/proxy-hosts          # 200
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{}' https://<host>/api/v1/proxy-hosts   # 403
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" \
  https://<host>/api/v1/settings              # 403
```

- [ ] **Step 2: Run both full suites**

```bash
docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings
docker exec megoopm-test ruff check app tests
cd frontend && npx vitest run && npx tsc --noEmit && npm run lint
```
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add docs
git commit -m "docs: what a member may do, and where the boundary is enforced

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 4: Tear down the test stack**

```bash
export MSYS_NO_PATHCONV=1
docker rm -f megoopm-test
```

---

## Manual verification

- [ ] Create a member user from the Users page as an admin, then sign in as them.
- [ ] The sidebar shows Dashboard through Security, with no Users or Settings.
- [ ] Every page listed loads with data — no 403 and no empty error state.
- [ ] No New / Edit / Delete button appears on any of them, and each shows the read-only note.
- [ ] The enable/disable toggles are present but inert.
- [ ] Typing `/settings` in the address bar returns to the Dashboard.
- [ ] `/profile` still works: change the member's own password, and enrol and remove a passkey.
- [ ] Sign in as an admin again and confirm every control is back.
