# CrowdSec Protection for Streams — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A per-stream toggle that drops connections from IPs CrowdSec has already banned, so an attacker locked out of port 443 is also locked out of the TCP forward on port 5432.

**Architecture:** `stream {}` gets its own Lua stack (separate VM, so its own `crowdsec_cache` and its own `SetupStream()` pull timer), reusing the existing init file unchanged. A new `preread_by_lua_file` handler calls the stock bouncer's `allowIp()` — a pure cache lookup with no http surface — and closes the connection on a non-allow. Attached per stream, exactly as `access_by_lua_file` is attached per host.

**Tech Stack:** OpenResty 1.25.3.2 + `lua-cs-bouncer` v1.0.8, Jinja2 nginx templates, FastAPI, SQLAlchemy + Alembic, Next.js 16 / React 19, vitest.

**Spec:** `docs/superpowers/specs/2026-09-06-stream-crowdsec-design.md`

## Global Constraints

- **Backend tests cannot run on Windows** (`app/services/cluster/locks.py` imports `fcntl`). Use the throwaway container; Postgres is required for the migration test and for `tests/test_meg24_api.py`:
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
  Run pytest **without** `-q`. Reuse the containers if they are already up.
- **Never run `alembic upgrade head` against `megoopm-testdb` casually.** It seeds `instance_settings` id=1, and `tests/test_settings_api.py` then fails its own seed with a duplicate-key error. If you do run it, reset before the suite:
  ```bash
  docker exec megoopm-testdb psql -U megoopm -d megoopm -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
  ```
- **`tests/conftest.py`'s SQLite fixture builds an explicit table list**, and `Stream` is not in it. Stream API tests therefore live in `tests/test_meg24_api.py`, which uses Postgres and a stubbed `enqueue_nginx_reload`. Do not try to add stream tests to the SQLite `db_client`.
- **A Lua error in `preread` aborts the connection.** That is why the handler is wrapped in `pcall` and why it returns early when the module is missing. Fail-open is a requirement, not a nicety: MegooPM reloads nginx on every config change, and a fail-closed stream bouncer would break every stream for up to `UPDATE_FREQUENCY=10` seconds each time.
- **`allowIp` returns `true` for ALLOWED.** Verified by reading how `Allow()` consumes it. Inverting this drops all traffic or none.
- **Python writes on this machine produce CRLF.** Run `sed -i 's/\r$//'` on every file you write with a script before committing or running prettier.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  ```

---

### Task 1: The nginx side

The task the feature turns on, and the only one that can take an edge node down. It ships before any database column exists, because it is inert until something references the handler.

**Files:**
- Create: `infra/nginx/lua/megoopm_crowdsec_stream.lua`
- Modify: `infra/nginx/nginx.conf`

**Interfaces:**
- Produces: `/etc/nginx/lua/megoopm_crowdsec_stream.lua`, referenced later by `stream.conf.j2` as `preread_by_lua_file`.

- [ ] **Step 1: Write the handler**

Create `infra/nginx/lua/megoopm_crowdsec_stream.lua`:

```lua
-- CrowdSec bouncer — per-stream preread handler.
--
-- The stream-context sibling of megoopm_crowdsec.lua. Attached only to streams
-- the backend renders with the CrowdSec toggle on (see backend
-- app/templates/nginx/stream.conf.j2 -> `preread_by_lua_file`), so a stream with
-- the toggle off never references this file.
--
-- Why `allowIp` and not `Allow`: `Allow()` is the http entry point — it reads
-- ngx.req, runs AppSec, and writes an HTTP response, none of which exist at
-- layer 4. `allowIp(ip)` is a pure decision-cache lookup returning
-- (allowed, remediation, err). It is the same library, a different door.
--
-- Every remediation collapses to one action here. There is no page to serve and
-- no captcha to present on a TCP connection, so anything that is not "allow"
-- closes it.
--
-- Fails OPEN, deliberately and in two ways: an uninitialised module returns
-- early, and a runtime error is swallowed by pcall. MegooPM reloads nginx on
-- every configuration change, and the decision cache is empty for up to
-- UPDATE_FREQUENCY (10s) afterwards — failing closed would drop every stream
-- connection during that window, on every change, and would take the databases
-- behind these streams down with LAPI whenever LAPI was unreachable.

local csmod = _G.megoopm_crowdsec
if not csmod then
    ngx.log(ngx.ERR, "[megoopm] CrowdSec bouncer not initialised; allowing connection")
    return
end

local ok, allowed, _, err = pcall(csmod.allowIp, ngx.var.remote_addr)
if not ok then
    ngx.log(ngx.ERR, "[megoopm] CrowdSec stream check failed: ", tostring(allowed))
    return
end
if err ~= nil then
    ngx.log(ngx.ERR, "[megoopm] CrowdSec stream check error: ", tostring(err))
end

-- `allowIp` returns true for ALLOWED. Inverting this blocks everything.
if allowed == false then
    ngx.log(ngx.NOTICE, "[megoopm] CrowdSec dropped stream connection from ", ngx.var.remote_addr)
    return ngx.exit(ngx.ERROR)
end
```

- [ ] **Step 2: Give `stream {}` its own Lua stack**

In `infra/nginx/nginx.conf`, the `stream {}` block currently holds only the
include. Replace it with:

```nginx
stream {
    # --- CrowdSec bouncer, stream side ---
    # http{} and stream{} are separate Lua VMs in OpenResty: they share no
    # shared dictionaries and no init_by_lua state. So the decision cache and
    # the LAPI pull timer below are a SECOND copy of what http{} already has,
    # not a reference to it. There is no way to share one.
    #
    # The init file is the same one http{} uses, unchanged — it only requires
    # the module, calls init() with the rendered config, and publishes it on
    # _G. Enforcement is attached PER STREAM by the backend-generated configs
    # (preread_by_lua_file), so a stream with the toggle off is never bounced.
    lua_package_path "/etc/nginx/lua/?.lua;;";
    lua_shared_dict crowdsec_cache 50m;
    init_by_lua_file /etc/nginx/lua/megoopm_crowdsec_init.lua;

    # Arms the decision pull. Without it the cache stays empty and every check
    # allows — the same defect the http side hit (MEG-22 / D2), silent both
    # times because an empty cache is indistinguishable from "nobody is banned".
    init_worker_by_lua_block {
        if _G.megoopm_crowdsec and _G.megoopm_crowdsec.SetupStream then
            _G.megoopm_crowdsec.SetupStream()
        end
    }

    include /data/nginx/conf.d/stream/*.conf;
}
```

Keep whatever comment already sits above the `include` line — it explains why
the stream directory is separate — and place these directives before it.

- [ ] **Step 3: Prove the image still starts**

The config must load with no stream defined at all, because that is what every
existing install looks like:

```bash
export MSYS_NO_PATHCONV=1
docker build -t megoopm-nginx:streamprobe ./infra/nginx
docker run --rm megoopm-nginx:streamprobe openresty -t
```
Expected: "syntax is ok" / "test is successful". A failure here is an edge
outage on deploy — stop and fix before going further.

- [ ] **Step 4: Prove the module initialises in the stream VM**

```bash
docker rm -f mgm-streamcheck 2>/dev/null
docker run -d --name mgm-streamcheck megoopm-nginx:streamprobe openresty -g "daemon off;"
sleep 3
docker logs mgm-streamcheck 2>&1 | grep -i "megoopm\|crowdsec" | head
docker rm -f mgm-streamcheck
```
Expected: the init file's `[megoopm] CrowdSec bouncer initialised` notice, or its
"init failed" line if LAPI is unreachable — **both are acceptable here** (there
is no LAPI in this probe). What must NOT appear is a Lua *load* error such as
"module not found", which would mean the site lualib is not on the stream VM's
path.

- [ ] **Step 5: Commit**

```bash
git add infra/nginx
git commit -m "feat(nginx): a CrowdSec bouncer for the stream context

http{} and stream{} are separate Lua VMs, so this is a second decision
cache and a second pull timer rather than a reference to the http one.
The init file is reused unchanged.

Inert until something references the handler: enforcement is attached per
stream by the generated configs, exactly as it is per host.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: The rendered stream config

**Files:**
- Modify: `backend/app/templates/nginx/stream.conf.j2`, `backend/app/services/nginx/state.py`
- Test: `backend/tests/test_stream_crowdsec_render.py` (create)

**Interfaces:**
- Consumes: the handler path from Task 1.
- Produces: `StreamSpec.crowdsec_enabled: bool = False`; `preread_by_lua_file` in the rendered stream config.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_stream_crowdsec_render.py`:

```python
"""How a stream with CrowdSec protection renders.

The guard is per stream, like the http bouncer is per host: a stream with the
toggle off must not reference the handler at all, or the toggle is decorative.
"""

from __future__ import annotations

from app.services.nginx.renderer import render_stream_config
from app.services.nginx.state import CertificateSpec, DesiredState, StreamSpec

HANDLER = "preread_by_lua_file /etc/nginx/lua/megoopm_crowdsec_stream.lua;"


def _render(**over) -> str:
    stream = StreamSpec(id=3, incoming_port=5432, forward_host="10.0.0.9", forward_port=5432, **over)
    return render_stream_config(DesiredState(streams=(stream,)))["megoopm-stream-3.conf"]


def test_a_stream_without_the_toggle_never_mentions_the_bouncer() -> None:
    conf = _render()

    assert "crowdsec" not in conf.lower()
    assert "preread_by_lua_file" not in conf


def test_the_toggle_attaches_the_handler() -> None:
    conf = _render(crowdsec_enabled=True)

    assert HANDLER in conf


def test_a_udp_stream_is_guarded_too() -> None:
    # preread runs on the first datagram; a UDP forward is as exposed as a TCP
    # one and there is no reason for the toggle to silently not apply.
    conf = _render(crowdsec_enabled=True, tcp_forwarding=False, udp_forwarding=True)

    assert HANDLER in conf
    assert "udp;" in conf


def test_a_stream_doing_both_protocols_is_guarded_once() -> None:
    conf = _render(crowdsec_enabled=True, tcp_forwarding=True, udp_forwarding=True)

    assert conf.count(HANDLER) == 1


def test_tls_termination_does_not_displace_the_guard() -> None:
    # The certificate branch inserts directives in the same server block; the
    # guard must survive being rendered alongside them.
    cert = CertificateSpec(
        id=1, fullchain_path="/data/certs/1/fullchain.pem", privkey_path="/data/certs/1/privkey.pem"
    )
    conf = _render(crowdsec_enabled=True, certificate=cert)

    assert HANDLER in conf
    assert "ssl_certificate /data/certs/1/fullchain.pem;" in conf


def test_the_guard_precedes_the_proxy_pass() -> None:
    """Order is the whole mechanism: preread runs before the connection is
    proxied, and a guard rendered after proxy_pass would read as protection
    while protecting nothing."""
    conf = _render(crowdsec_enabled=True)

    assert conf.index(HANDLER) < conf.index("proxy_pass")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_stream_crowdsec_render.py -p no:cacheprovider -p no:warnings`
Expected: FAIL — `StreamSpec` has no `crowdsec_enabled`.

- [ ] **Step 3: Add the spec field**

In `backend/app/services/nginx/state.py`, on `StreamSpec`, after `udp_forwarding`:

```python
    # CrowdSec: drop connections from IPs already carrying a decision. Layer 4
    # cannot detect an attack, only refuse an IP something else already judged.
    crowdsec_enabled: bool = False
```

- [ ] **Step 4: Render it**

In `backend/app/templates/nginx/stream.conf.j2`, inside the `server {`, before
the `proxy_pass` (order matters — see the test):

```jinja
{%- if stream.crowdsec_enabled %}

    # CrowdSec bouncer, stream side. `preread` is the only phase available here:
    # stream{} has no access phase. The handler drops connections from IPs with
    # an active decision and allows everything else, including when the decision
    # cache is empty.
    preread_by_lua_file /etc/nginx/lua/megoopm_crowdsec_stream.lua;
{%- endif %}
```

- [ ] **Step 5: Run the tests**

```bash
docker exec megoopm-test python -m pytest tests/test_stream_crowdsec_render.py tests/test_nginx_render.py tests/test_meg24_render.py -p no:cacheprovider -p no:warnings
```
Expected: the 6 new tests pass and the existing render suites are unchanged —
they render streams without the toggle, so nothing there should move.

- [ ] **Step 6: Prove the rendered config loads in the real image**

Text assertions do not establish that nginx accepts it:

```bash
export MSYS_NO_PATHCONV=1
docker exec megoopm-test python -c "
from app.services.nginx.renderer import render_stream_config
from app.services.nginx.state import DesiredState, StreamSpec
s = StreamSpec(id=3, incoming_port=5432, forward_host='127.0.0.1', forward_port=9,
               crowdsec_enabled=True)
print(render_stream_config(DesiredState(streams=(s,)))['megoopm-stream-3.conf'])
"
```
Save that output to a file, mount it at `/data/nginx/conf.d/stream/megoopm-stream-3.conf`
in `megoopm-nginx:streamprobe` (built in Task 1), and run `openresty -t`.
Expected: successful. Use the scratchpad directory for the temporary files.

- [ ] **Step 7: Commit**

```bash
git add backend/app/templates backend/app/services/nginx/state.py backend/tests/test_stream_crowdsec_render.py
git commit -m "feat(streams): render the CrowdSec guard when a stream asks for it

Per stream, like the http bouncer is per host: a stream with the toggle
off never references the handler. preread, because stream{} has no access
phase — and before proxy_pass, because a guard after it would read as
protection while protecting nothing.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Storage and API

**Files:**
- Modify: `backend/app/models/stream.py`, `backend/app/schemas/stream.py`, `backend/app/services/nginx/loader.py`, `backend/openapi.json`
- Create: `backend/alembic/versions/0037_stream_crowdsec.py`
- Test: `backend/tests/test_stream_crowdsec_migration.py` (create), `backend/tests/test_meg24_api.py` (modify)

**Interfaces:**
- Consumes: `StreamSpec.crowdsec_enabled` from Task 2.
- Produces: `Stream.crowdsec_enabled`; the field on `StreamBase` and `StreamUpdate`.

- [ ] **Step 1: Write the failing migration test**

Create `backend/tests/test_stream_crowdsec_migration.py`, copying the schema
harness from `tests/test_maintenance_migration.py` verbatim (the `_exec`,
`_set_role_search_path`, `_reset_schema` helpers and the `migrated` fixture),
with `SCHEMA = "stream_crowdsec_probe"` and these tests:

```python
def test_a_stream_is_not_protected_by_default(migrated) -> None:
    migrated("0037_stream_crowdsec")
    asyncio.run(
        _exec(
            [
                "INSERT INTO streams (id, incoming_port, forward_host, forward_port,"
                " tcp_forwarding, udp_forwarding, enabled)"
                " VALUES (1, 5432, '10.0.0.9', 5432, true, false, true)",
            ]
        )
    )

    rows = asyncio.run(_exec(["SELECT crowdsec_enabled FROM streams WHERE id = 1"]))

    # Switching on a control that silently drops traffic is never something an
    # upgrade does for you.
    assert [tuple(r) for r in rows] == [(False,)]


def test_the_column_holds_the_choice(migrated) -> None:
    migrated("0037_stream_crowdsec")
    asyncio.run(
        _exec(
            [
                "INSERT INTO streams (id, incoming_port, forward_host, forward_port,"
                " tcp_forwarding, udp_forwarding, enabled, crowdsec_enabled)"
                " VALUES (1, 5432, '10.0.0.9', 5432, true, false, true, true)",
            ]
        )
    )

    rows = asyncio.run(_exec(["SELECT crowdsec_enabled FROM streams WHERE id = 1"]))

    assert [tuple(r) for r in rows] == [(True,)]
```

Check the real column list of `streams` before running (`\d streams`, or
`backend/app/models/stream.py`) and match the INSERT to it — a check constraint
on the forward target will reject a row that sets neither.

- [ ] **Step 2: Run it to verify it fails**

Run: `docker exec megoopm-test python -m pytest tests/test_stream_crowdsec_migration.py -p no:cacheprovider -p no:warnings`
Expected: FAIL — revision `0037_stream_crowdsec` does not exist.

- [ ] **Step 3: Add the column and the migration**

In `backend/app/models/stream.py`, on `Stream`, after `udp_forwarding`:

```python
    # CrowdSec: drop connections from IPs already carrying a decision (MEG-22's
    # http bouncer, stream side). Off by default — see the migration.
    crowdsec_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
```

Create `backend/alembic/versions/0037_stream_crowdsec.py`:

```python
"""Streams can be protected by CrowdSec

Off for every existing stream: enabling a control that silently drops
connections is a decision an operator makes, never one an upgrade makes for
them.

Revision ID: 0037_stream_crowdsec
Revises: 0036_api_keys
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0037_stream_crowdsec"
down_revision: str | None = "0036_api_keys"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "streams",
        sa.Column("crowdsec_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("streams", "crowdsec_enabled")
```

Confirm the table is named `streams` (`__tablename__` in the model) before
writing the migration.

- [ ] **Step 4: Add the schema field and wire the loader**

In `backend/app/schemas/stream.py`, on `StreamBase`, after `udp_forwarding`:

```python
    crowdsec_enabled: bool = Field(
        default=False,
        description=(
            "Drop connections from IPs CrowdSec has banned. Enforcement only: a "
            "TCP forward carries no application signal, so CrowdSec cannot detect "
            "attacks against this port itself."
        ),
    )
```

Add `crowdsec_enabled: bool | None = None` to `StreamUpdate` beside the other
optional flags.

In `backend/app/services/nginx/loader.py` (~line 473), inside the `StreamSpec(...)`
construction, after `udp_forwarding=s.udp_forwarding,`:

```python
                crowdsec_enabled=s.crowdsec_enabled,
```

- [ ] **Step 5: Add the API round-trip test**

In `backend/tests/test_meg24_api.py`, beside `test_stream_crud_and_render`:

```python
async def test_a_stream_can_be_protected_by_crowdsec(client: AsyncClient, auth) -> None:
    created = await client.post(
        "/api/v1/streams",
        json={
            "incoming_port": 5433,
            "forward_host": "10.0.0.9",
            "forward_port": 5432,
            "tcp_forwarding": True,
            "crowdsec_enabled": True,
        },
        headers=auth,
    )

    assert created.status_code == 201, created.text
    assert created.json()["crowdsec_enabled"] is True

    off = await client.patch(
        f"/api/v1/streams/{created.json()['id']}",
        json={"crowdsec_enabled": False},
        headers=auth,
    )
    assert off.json()["crowdsec_enabled"] is False


async def test_a_stream_is_unprotected_unless_asked(client: AsyncClient, auth) -> None:
    created = await client.post(
        "/api/v1/streams",
        json={"incoming_port": 5434, "forward_host": "10.0.0.9", "forward_port": 5432},
        headers=auth,
    )

    assert created.json()["crowdsec_enabled"] is False
```

Match the create payload to whatever `test_stream_crud_and_render` already sends
— the schema requires exactly one forward target.

- [ ] **Step 6: Run the tests and regenerate the contract**

```bash
docker exec megoopm-test python -m pytest tests/test_stream_crowdsec_migration.py tests/test_meg24_api.py -p no:cacheprovider -p no:warnings
docker exec megoopm-test python -m scripts.export_openapi
docker exec megoopm-test ruff check app tests alembic
docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings
```
Expected: all pass. If `test_settings_api.py` errors with a duplicate
`instance_settings` key, the migration test left the schema seeded — reset it
with the command in Global Constraints and re-run.

- [ ] **Step 7: Commit**

```bash
git add backend
git commit -m "feat(streams): store and expose the CrowdSec toggle

Off for every existing stream. Enabling a control that silently drops
connections is a decision an operator makes, not one an upgrade makes for
them.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The UI

**Files:**
- Modify: `frontend/src/components/streams/stream-dialog.tsx`, `frontend/src/components/streams/streams-view.tsx`
- Test: `frontend/src/components/streams/stream-dialog.test.tsx`, `frontend/src/components/streams/streams-view.test.tsx`

**Interfaces:**
- Consumes: `crowdsec_enabled` from Task 3.
- Produces: the toggle in the dialog's Details tab and a shield badge on the list.

- [ ] **Step 1: Regenerate types**

```bash
cd frontend && npm run gen:api
```

- [ ] **Step 2: Write the failing tests**

Add to `frontend/src/components/streams/stream-dialog.test.tsx`:

```tsx
  it("offers CrowdSec protection and saves it", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(await screen.findByLabelText("CrowdSec protection"));
    await user.click(screen.getByRole("button", { name: /save|create/i }));

    await waitFor(() => expect(streams.update).toHaveBeenCalled());
    expect(vi.mocked(streams.update).mock.calls[0][1]).toMatchObject({
      crowdsec_enabled: true,
    });
  });

  it("says what the toggle cannot do", async () => {
    // An operator who reads it as "CrowdSec now watches this port" has been
    // misled: a TCP forward carries no application signal.
    renderDialog();

    expect(await screen.findByText(/cannot detect attacks against this port/i)).toBeInTheDocument();
  });
```

Match `renderDialog`'s existing shape in that file — it may render a create
dialog (asserting on `streams.create`) rather than an edit one.

Add to `frontend/src/components/streams/streams-view.test.tsx`:

```tsx
  it("marks a protected stream in the list", async () => {
    // Otherwise the only signal is inside a dialog, and "is this port guarded?"
    // is exactly the question asked while scanning.
    vi.mocked(streams.list).mockResolvedValue([makeStream({ crowdsec_enabled: true })]);
    render(<StreamsView />);

    expect(await screen.findByLabelText("Protected by CrowdSec")).toBeInTheDocument();
  });

  it("marks nothing when the stream is unprotected", async () => {
    render(<StreamsView />);
    await screen.findByRole("searchbox");

    expect(screen.queryByLabelText("Protected by CrowdSec")).not.toBeInTheDocument();
  });
```

Read that file's existing mount helper and stream factory first and reuse them;
if there is no `makeStream`, build the row the way the file already does.

- [ ] **Step 3: Run them to verify they fail**

Run: `cd frontend && npx vitest run src/components/streams`
Expected: the four new tests FAIL.

- [ ] **Step 4: Add the toggle**

In `stream-dialog.tsx`: add `crowdsecEnabled: boolean` to `FormState`, read it in
both branches of `stateFromStream` (`false` for a new stream,
`stream.crowdsec_enabled` for an existing one), and send
`crowdsec_enabled: form.crowdsecEnabled` in `payload`.

Render it with the file's own `ToggleRow`, beside the TCP/UDP/Enabled rows:

```tsx
              <ToggleRow
                className="sm:col-span-2"
                label="CrowdSec protection"
                hint="Drop connections from IPs CrowdSec has banned — from your HTTP traffic and the community blocklist. Streams are forwarded at the TCP level, so CrowdSec cannot detect attacks against this port itself."
                checked={form.crowdsecEnabled}
                onCheckedChange={(v) => setForm((p) => ({ ...p, crowdsecEnabled: v }))}
                disabled={saving}
              />
```

Check `ToggleRow`'s real props in the same file — it is defined there, and the
label must reach the switch's accessible name for `getByLabelText` to find it.

- [ ] **Step 5: Add the list badge**

In `streams-view.tsx`, in the Protocols or TLS cell, beside the existing badges:

```tsx
                        {stream.crowdsec_enabled ? (
                          <ShieldCheck
                            className="size-3.5 text-emerald-600 dark:text-emerald-400"
                            aria-label="Protected by CrowdSec"
                          />
                        ) : null}
```

Import `ShieldCheck` from `lucide-react`. An icon rather than a column: the table
already carries six columns and this is a yes/no fact.

- [ ] **Step 6: Run the frontend gate**

```bash
cd frontend && npx vitest run && npx tsc --noEmit && npm run lint
npx prettier --check --print-width 100 src/components/streams/*.tsx
```
Expected: all pass. Adding a required field to the generated `Stream` type may
break fixtures in other suites — add `crowdsec_enabled: false` where the
compiler points, and check each edit is a fixture and not an assertion.

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat(streams): a CrowdSec toggle, and what it cannot do

The hint carries the boundary: this drops IPs CrowdSec already banned
from HTTP traffic and the community blocklist, and cannot detect an
attack on the port itself. An operator who reads the toggle as 'CrowdSec
now watches this port' has been misled by us.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Prove it actually blocks

The task that decides whether the feature works at all. Everything before this
establishes that the right text is generated and the right config loads; **none
of it establishes that a banned IP is dropped**, and fail-open means a broken
bouncer is indistinguishable from "nobody is banned".

**Files:**
- Modify: `docs/crowdsec.md`

- [ ] **Step 1: Stand up a real LAPI and a guarded stream**

On a machine with the stack running (or a throwaway compose with `crowdsec` +
`nginx`), create a stream with the toggle on, forwarding to something that
answers — an echo server or any container port.

Confirm the bouncer registered from the stream context:

```bash
docker compose exec crowdsec cscli bouncers list
```
Expected: the bouncer shows a recent "Last API pull". If "Last API pull" is
empty, `SetupStream()` did not arm in the stream VM — that is the spec's named
residual risk, and it must be fixed here rather than shipped.

- [ ] **Step 2: Prove an unbanned connection succeeds**

```bash
nc -vz <host> <stream-port>
```
Expected: connected. Establishes the guard is not dropping everything, which a
polarity inversion would.

- [ ] **Step 3: Ban the client and prove the connection is dropped**

```bash
docker compose exec crowdsec cscli decisions add --ip <your-client-ip> --duration 5m --reason "stream bouncer test"
sleep 12   # UPDATE_FREQUENCY=10: the stream VM pulls on its own timer
nc -vz <host> <stream-port>
```
Expected: refused / connection closed. Check the nginx log for the handler's
`CrowdSec dropped stream connection from ...` line.

- [ ] **Step 4: Prove the ban lifts**

```bash
docker compose exec crowdsec cscli decisions delete --ip <your-client-ip>
sleep 12
nc -vz <host> <stream-port>
```
Expected: connected again. A guard that never releases is worse than none.

- [ ] **Step 5: Prove an unprotected stream is untouched**

Create a second stream with the toggle **off**, ban the client, and connect.
Expected: connected. The toggle must be per stream, not global.

- [ ] **Step 6: Document it**

In `docs/crowdsec.md`, beside the bouncer section, add a "Streams" subsection
stating:

- streams are protected by a per-stream toggle, off by default;
- it is **enforcement only** — CrowdSec receives no stream logs, so it cannot
  detect an attack on the port itself, only refuse an IP judged elsewhere;
- `stream {}` runs a second Lua VM with its own decision cache and its own LAPI
  pull, because OpenResty contexts share neither;
- it **fails open**: an empty cache allows, so a reload leaves up to
  `UPDATE_FREQUENCY` (10s) unenforced, and a LAPI outage leaves streams open
  rather than closed;
- how to verify it, using the `cscli decisions add` / `nc` sequence above.

- [ ] **Step 7: Run both full suites, then commit**

```bash
docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings
docker exec megoopm-test ruff check app tests alembic
cd frontend && npx vitest run && npx tsc --noEmit && npm run lint
```

```bash
git add docs
git commit -m "docs(crowdsec): stream protection, and the two things it is not

Enforcement without detection, and fail-open. Both are deliberate; both
look like bugs to someone who does not know.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 8: Tear down** (only if no further backend work is queued)

```bash
export MSYS_NO_PATHCONV=1
docker rm -f megoopm-test megoopm-testdb && docker network rm megoopm-testnet
docker rmi megoopm-nginx:streamprobe
```

---

## Manual verification

- [ ] Rebuild the nginx image and confirm every existing install still starts with no streams defined.
- [ ] `docker compose exec crowdsec cscli bouncers list` shows a recent pull.
- [ ] A stream with the toggle **on** refuses a banned IP and serves an unbanned one.
- [ ] A stream with the toggle **off** serves the banned IP — the toggle is per stream.
- [ ] The ban lifting restores the connection within ~10s.
- [ ] Restarting CrowdSec leaves the streams **reachable**, not dead (fail-open).
- [ ] The streams list shows the shield on the protected stream only.
