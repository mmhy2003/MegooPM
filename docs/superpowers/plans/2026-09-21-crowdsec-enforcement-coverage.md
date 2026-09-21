# CrowdSec Enforcement Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refuse CrowdSec-banned IPs on redirection hosts, 404 hosts, the default sites and the Force-SSL :80 server of proxy hosts — not only on proxy hosts' access phase.

**Architecture:** Those servers answer with `return`, which runs in nginx's rewrite phase, before the access phase the bouncer uses today. They get the same bouncer handler through `server_rewrite_by_lua_file`, which runs first. The handler body moves into a shared Lua module so the base default server can call it inline (with `/healthz` exempt). Redirection and 404 hosts get a `crowdsec_enabled` column, on by default.

**Tech Stack:** OpenResty 1.25.3.2 + vendored lua-cs-bouncer, CrowdSec 1.6.4, FastAPI/SQLAlchemy/Alembic, Jinja2 templates, Next.js + vitest.

**Spec:** `docs/superpowers/specs/2026-09-21-crowdsec-enforcement-coverage-design.md`

## Global Constraints

- The bouncer check on new server types is IP bans **and** AppSec — the same `Allow()` proxy hosts run. Do not render `$crowdsec_disable_appsec`.
- `crowdsec_enabled` on `redirection_hosts` and `dead_hosts`: `NOT NULL`, server default `true`; create-schema default `True`.
- Proxy hosts: only the :80 server with a certificate **and** `ssl_forced` changes to the server-rewrite hook. Every other proxy server keeps `access_by_lua_file`.
- The default :80 server exempts exactly `/healthz`.
- The "not initialised" error logs once per worker, not per request.
- **Task 1 is a gate.** If any of its checks fails, stop and report; do not start Task 2.
- Backend tests run in the `megoopm-test` container (`docker exec -w /src megoopm-test python -m pytest …`, `export MSYS_NO_PATHCONV=1` first). Tests that read files outside `backend/` (compose, `infra/`) run in a throwaway container with the repo mounted (see Task 5).
- Files written from Windows: strip CR (`sed -i 's/\r$//' <file>`) before committing.
- Frontend prettier: format touched files at the width their `HEAD` version is clean at (check both `--print-width 80` and `100`); never format `src/lib/api/generated/schema.ts`.
- Commits end with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

---

### Task 1: Prove the bouncer works in the server-rewrite phase (gate)

**Files:**
- Create: `infra/nginx/tests/bouncer-phase.sh`

**Interfaces:**
- Produces: a re-runnable end-to-end harness. Tasks 2 and 5 re-run it; each adds cases.

The vendored module was written for the access phase. This harness runs the real CrowdSec and the real MegooPM nginx image side by side and compares today's hook (reference) with the new one.

- [ ] **Step 1: Write the harness**

```bash
#!/usr/bin/env bash
# End-to-end check of the CrowdSec bouncer in nginx's server-rewrite phase.
#
# Runs a real CrowdSec 1.6.4 and the MegooPM nginx image on a private network
# and compares, request by request, the access-phase hook proxy hosts use
# (reference, :8001) with the server-rewrite hook (:8002 server-level return,
# :8003 location-level return). Exit status is the verdict.
#
#   MSYS_NO_PATHCONV=1 bash infra/nginx/tests/bouncer-phase.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
NET=megoopm-bouncer-probe
KEY=probe-bouncer-key-0123456789abcdef0123
WORK="$(mktemp -d)"
FAILED=0

cleanup() {
  docker rm -f bp-crowdsec bp-nginx bp-client >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT
cleanup

docker build -q -t megoopm-nginx-probe "$REPO/infra/nginx" >/dev/null
docker network create "$NET" >/dev/null

docker run -d --name bp-crowdsec --network "$NET" --network-alias crowdsec \
  -e DISABLE_ONLINE_API=true -e BOUNCER_KEY_megoopm="$KEY" \
  -e COLLECTIONS="crowdsecurity/appsec-virtual-patching crowdsecurity/appsec-generic-rules" \
  -v "$REPO/infra/crowdsec/acquis/appsec.yaml:/etc/crowdsec/acquis.d/appsec.yaml:ro" \
  crowdsecurity/crowdsec:v1.6.4 >/dev/null
for _ in $(seq 60); do
  docker exec bp-crowdsec cscli lapi status >/dev/null 2>&1 && break
  sleep 2
done

mkdir -p "$WORK/nginx/conf.d/stream" "$WORK/nginx/default"
cat > "$WORK/nginx/conf.d/probe.conf" <<'EOF'
server { listen 8001; access_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;
         location / { return 200 "reached\n"; } }
server { listen 8002; server_rewrite_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;
         return 302 http://target.example/; }
server { listen 8003; server_rewrite_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;
         location / { return 404; } }
EOF
docker run -d --name bp-nginx --network "$NET" \
  -e CROWDSEC_LAPI_URL=http://crowdsec:8080 -e CROWDSEC_APPSEC_URL=http://crowdsec:7422 \
  -e CROWDSEC_BOUNCER_KEY="$KEY" \
  -v "$REPO/infra/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" \
  -v "$WORK:/data" megoopm-nginx-probe >/dev/null
docker run -d --name bp-client --network "$NET" --entrypoint sleep curlimages/curl 600 >/dev/null
CLIENT_IP="$(docker inspect -f "{{(index .NetworkSettings.Networks \"$NET\").IPAddress}}" bp-client)"
sleep 5

status() { # port path [curl args...]
  local port="$1" path="$2"; shift 2
  docker exec bp-client curl -s -o /dev/null -w '%{http_code}' "$@" "http://bp-nginx:$port$path"
}
expect() { # label expected actual
  if [ "$2" = "$3" ]; then echo "ok    $1 ($3)"; else echo "FAIL  $1: expected $2, got $3"; FAILED=1; fi
}
wait_for_stream() { sleep 12; } # bouncer UPDATE_FREQUENCY=10

# 1. A clean IP reaches each server's own answer.
expect "clean, access reference"      200 "$(status 8001 /)"
expect "clean, server-level return"   302 "$(status 8002 /)"
expect "clean, location-level return" 404 "$(status 8003 /)"

# 2. A POST body is readable in this phase (AppSec reads it) and passes clean.
expect "clean POST, server-level return" 302 "$(status 8002 /form -X POST --data 'name=alice')"

# 3. AppSec blocks a virtual-patching probe in the new phase as in the old.
REF="$(status 8001 /.env)"
expect "AppSec probe, reference blocks"        403 "$REF"
expect "AppSec probe, server-level return"     "$REF" "$(status 8002 /.env)"
expect "AppSec probe, location-level return"   "$REF" "$(status 8003 /.env)"

# 4. A ban refuses the client on every hook.
docker exec bp-crowdsec cscli decisions add --ip "$CLIENT_IP" --duration 1h >/dev/null
wait_for_stream
expect "ban, access reference"      403 "$(status 8001 /)"
expect "ban, server-level return"   403 "$(status 8002 /)"
expect "ban, location-level return" 403 "$(status 8003 /)"

# 5. A captcha decision behaves exactly as it does on the reference hook.
docker exec bp-crowdsec cscli decisions delete --ip "$CLIENT_IP" >/dev/null
docker exec bp-crowdsec cscli decisions add --ip "$CLIENT_IP" --type captcha --duration 1h >/dev/null
wait_for_stream
REF="$(status 8001 /)"
expect "captcha, server-level return"   "$REF" "$(status 8002 /)"
expect "captcha, location-level return" "$REF" "$(status 8003 /)"

[ "$FAILED" = 0 ] && echo "PASS" || { echo "FAILED"; docker logs bp-nginx 2>&1 | tail -30; }
exit "$FAILED"
```

- [ ] **Step 2: Run it**

Run: `MSYS_NO_PATHCONV=1 bash infra/nginx/tests/bouncer-phase.sh`
Expected: every line `ok`, final `PASS`.

If the harness itself misbehaves (nginx does not start, CrowdSec never answers), fix the harness — read `infra/nginx/docker-entrypoint.sh` for what the image needs — and re-run. A **check** that fails with a working harness is the gate: stop, report the failing line and the nginx log tail, and do not continue.

- [ ] **Step 3: Commit**

```bash
sed -i 's/\r$//' infra/nginx/tests/bouncer-phase.sh
git add infra/nginx/tests/bouncer-phase.sh
git commit -m "test(nginx): end-to-end check of the bouncer in the server-rewrite phase"
```

---

### Task 2: Shared Lua check, logging "not initialised" once per worker

**Files:**
- Create: `infra/nginx/lua/megoopm_crowdsec_check.lua`
- Modify: `infra/nginx/lua/megoopm_crowdsec.lua` (whole file)
- Modify: `infra/nginx/tests/bouncer-phase.sh` (new case)

**Interfaces:**
- Produces: `require("megoopm_crowdsec_check").check()` — no arguments, no return value; applies CrowdSec remediation to the current request or lets it continue. Resolvable because `nginx.conf` sets `lua_package_path "/etc/nginx/lua/?.lua;;"`.

- [ ] **Step 1: Add the failing harness case**

Append before the final `[ "$FAILED" = 0 ]` line of `bouncer-phase.sh`:

```bash
# 6. Without CrowdSec, requests pass and the error is logged once per worker,
#    not once per request (default sites now call this for every scanner hit).
docker rm -f bp-nginx >/dev/null
docker run -d --name bp-nginx --network "$NET" \
  -e CROWDSEC_LAPI_URL=http://nowhere.invalid:8080 -e CROWDSEC_APPSEC_URL= \
  -e CROWDSEC_BOUNCER_KEY= \
  -v "$REPO/infra/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" \
  -v "$WORK:/data" megoopm-nginx-probe >/dev/null
sleep 5
if docker logs bp-nginx 2>&1 | grep -q "CrowdSec bouncer initialised"; then
  echo "skip  uninitialised case: the bouncer initialised without credentials"
else
  # More requests than workers, or a per-request logger could pass unnoticed
  # on a many-core machine (worker_processes auto).
  WORKERS="$(docker exec bp-nginx sh -c 'ps -o args | grep -c "[n]ginx: worker"')"
  for _ in $(seq $((WORKERS * 3 + 3))); do status 8002 / >/dev/null; done
  LINES="$(docker logs bp-nginx 2>&1 | grep -c 'bouncer not initialised' || true)"
  expect "uninitialised, request passes" 302 "$(status 8002 /)"
  [ "$LINES" -le "$WORKERS" ] && echo "ok    logged $LINES time(s) for $WORKERS worker(s)" \
    || { echo "FAIL  logged $LINES times for $WORKERS worker(s)"; FAILED=1; }
fi
```

- [ ] **Step 2: Run it to see the new case fail**

Run: `MSYS_NO_PATHCONV=1 bash infra/nginx/tests/bouncer-phase.sh`
Expected: cases 1–5 `ok`; case 6 `FAIL  logged N times for M worker(s)` with N > M (once per request). If it prints `skip`, the bouncer initialises without credentials in this image; record that in the commit message and continue — the log-once change is still correct.

- [ ] **Step 3: Write the module**

`infra/nginx/lua/megoopm_crowdsec_check.lua`:

```lua
-- CrowdSec bouncer check, shared by every server block that enforces bans.
--
-- Two entry points call it: megoopm_crowdsec.lua (generated hosts, in the
-- access or server-rewrite phase) and the base default server's inline block,
-- which exempts /healthz. The stock bouncer's `Allow()` applies any IP decision
-- and, when AppSec is configured, forwards the request to the WAF; on a hit it
-- ends the request itself.
--
-- It fails open: if the module never initialised (CrowdSec not configured),
-- requests are allowed. That is logged once per worker, not per request —
-- the default sites call this for every scanner hit, and a line per hit would
-- bury every other error.
local M = {}

local warned = false

function M.check()
    local csmod = _G.megoopm_crowdsec
    if not csmod then
        if not warned then
            warned = true
            ngx.log(ngx.ERR, "[megoopm] CrowdSec bouncer not initialised; allowing requests",
                " (logged once per worker)")
        end
        return
    end

    local ip = ngx.var.remote_addr
    local ok, err = pcall(function()
        csmod.Allow(ip)
    end)
    if not ok then
        -- Failing open on a Lua-level error beats blocking every host. AppSec's
        -- own posture on an AppSec-backend error is APPSEC_FAILURE_ACTION in
        -- crowdsec-bouncer.conf, inside the module.
        ngx.log(ngx.ERR, "[megoopm] CrowdSec check error for ", ip, ": ", tostring(err))
    end
end

return M
```

- [ ] **Step 4: Reduce the host handler to the module call**

Replace the whole of `infra/nginx/lua/megoopm_crowdsec.lua` with:

```lua
-- CrowdSec bouncer — handler for generated server blocks (MEG-22).
--
-- Rendered as `access_by_lua_file` into proxy-host servers, and as
-- `server_rewrite_by_lua_file` into servers that answer with `return`
-- (redirection hosts, 404 hosts, default-TLS sites, a proxy host's Force-SSL
-- :80 server): `return` runs in the rewrite phase, before access, so an
-- access-phase check there would never run. The check itself — and why it
-- fails open — lives in megoopm_crowdsec_check.lua.
require("megoopm_crowdsec_check").check()
```

- [ ] **Step 5: Run the harness**

Run: `MSYS_NO_PATHCONV=1 bash infra/nginx/tests/bouncer-phase.sh`
Expected: every line `ok` (or case 6 `skip`), final `PASS`.

- [ ] **Step 6: Commit**

```bash
sed -i 's/\r$//' infra/nginx/lua/*.lua infra/nginx/tests/bouncer-phase.sh
git add infra/nginx/lua infra/nginx/tests/bouncer-phase.sh
git commit -m "refactor(nginx): one CrowdSec check module, logging 'not initialised' once"
```

---

### Task 3: `crowdsec_enabled` on redirection and 404 hosts

**Files:**
- Create: `backend/alembic/versions/0038_crowdsec_more_hosts.py`
- Create: `backend/tests/test_crowdsec_more_hosts_migration.py`
- Modify: `backend/app/models/redirection_host.py`, `backend/app/models/dead_host.py`
- Modify: `backend/app/schemas/redirection_host.py`, `backend/app/schemas/dead_host.py`
- Modify: `backend/app/services/nginx/state.py` (`RedirectionHostSpec`, `DeadHostSpec`)
- Modify: `backend/app/services/nginx/loader.py` (`_load_redirection_hosts`, `_load_dead_hosts`)
- Modify: `backend/tests/test_meg24_api.py`
- Regenerate: `backend/openapi.json`, `frontend/src/lib/api/generated/schema.ts`

**Interfaces:**
- Produces: `RedirectionHostSpec.crowdsec_enabled: bool = True`, `DeadHostSpec.crowdsec_enabled: bool = True` (Task 4 renders from them); API field `crowdsec_enabled` on read/create/update for both host types (Task 6 uses it).

- [ ] **Step 1: Write the failing API tests**

Append to `backend/tests/test_meg24_api.py`:

```python
async def test_new_redirection_and_404_hosts_enforce_crowdsec_by_default(
    client: AsyncClient, auth
) -> None:
    # On by default: a redirect or parked domain serves no app a ban could
    # break, and a host that silently ignores bans is the gap this closes.
    redirect = await client.post(
        "/api/v1/redirection-hosts",
        headers=auth,
        json={"domain_names": ["r.example.com"], "forward_domain_name": "t.example.com"},
    )
    dead = await client.post(
        "/api/v1/dead-hosts", headers=auth, json={"domain_names": ["d.example.com"]}
    )
    assert redirect.json()["crowdsec_enabled"] is True
    assert dead.json()["crowdsec_enabled"] is True


async def test_crowdsec_can_be_switched_off_per_host(client: AsyncClient, auth) -> None:
    created = await client.post(
        "/api/v1/dead-hosts", headers=auth, json={"domain_names": ["d2.example.com"]}
    )
    host_id = created.json()["id"]

    patched = await client.patch(
        f"/api/v1/dead-hosts/{host_id}", headers=auth, json={"crowdsec_enabled": False}
    )

    assert patched.status_code == 200
    assert patched.json()["crowdsec_enabled"] is False
    fetched = await client.get(f"/api/v1/dead-hosts/{host_id}", headers=auth)
    assert fetched.json()["crowdsec_enabled"] is False
```

- [ ] **Step 2: Write the failing migration test**

`backend/tests/test_crowdsec_more_hosts_migration.py` — copy the harness helpers (`SCHEMA`, `_exec`, `_set_role_search_path`, `_reset_schema`, the `migrated` fixture) verbatim from `backend/tests/test_nginx_apply_status_migration.py`, with `SCHEMA = "crowdsec_more_hosts_probe"` and this docstring, then add:

```python
"""0038 against a real Postgres: existing redirection and 404 hosts come out on.

Uses the same throwaway-schema harness as tests/test_nginx_apply_status_migration.py,
so running it never leaves the shared test database seeded.
"""


def test_existing_hosts_are_switched_on(migrated) -> None:
    # The gap closes on deploy: every host that existed before enforces bans.
    migrated("0037_nginx_apply_status")
    asyncio.run(
        _exec(
            [
                "INSERT INTO redirection_hosts (domain_names, forward_domain_name)"
                " VALUES ('{r.example.com}', 't.example.com')",
                "INSERT INTO dead_hosts (domain_names) VALUES ('{d.example.com}')",
            ]
        )
    )

    migrated("0038_crowdsec_more_hosts")

    rows = asyncio.run(
        _exec(
            [
                "SELECT crowdsec_enabled FROM redirection_hosts"
                " UNION ALL SELECT crowdsec_enabled FROM dead_hosts"
            ]
        )
    )
    assert [tuple(r) for r in rows] == [(True,), (True,)]
```

If the `INSERT`s fail on other `NOT NULL` columns without defaults, add those columns with valid values — read the 0037-era column list with `\d redirection_hosts` in `megoopm-testdb` against the probe schema, or from the models. Keep the assertion unchanged.

- [ ] **Step 3: Run both to see them fail**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_meg24_api.py tests/test_crowdsec_more_hosts_migration.py`
Expected: the two API tests fail with `KeyError: 'crowdsec_enabled'`; the migration test fails with alembic `Can't locate revision identified by '0038_crowdsec_more_hosts'`.

- [ ] **Step 4: Write the migration**

`backend/alembic/versions/0038_crowdsec_more_hosts.py`:

```python
"""Enforce CrowdSec bans on redirection and 404 hosts

Until now only proxy hosts ran the bouncer, so an IP CrowdSec banned for what
it did against a redirect or a parked domain was never blocked there. The
column defaults to true, and the server default fills existing rows: the gap
closes on deploy rather than host by host.

Revision ID: 0038_crowdsec_more_hosts
Revises: 0037_nginx_apply_status
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0038_crowdsec_more_hosts"
down_revision: str | None = "0037_nginx_apply_status"
branch_labels: str | None = None
depends_on: str | None = None

_TABLES = ("redirection_hosts", "dead_hosts")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("crowdsec_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "crowdsec_enabled")
```

- [ ] **Step 5: Models, schemas, specs, loader**

In both `backend/app/models/redirection_host.py` and `backend/app/models/dead_host.py`, after the `hsts_subdomains` column:

```python
    # Refuse IPs CrowdSec has banned (and run AppSec). On by default: these
    # hosts answer with `return`, so the bouncer runs in the server-rewrite
    # phase — see server_rewrite_by_lua in the templates.
    crowdsec_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
```

In `RedirectionHostBase` (`backend/app/schemas/redirection_host.py`) after `block_exploits`, and in `DeadHostBase` (`backend/app/schemas/dead_host.py`) after `hsts_subdomains`:

```python
    crowdsec_enabled: bool = Field(
        default=True, description="Refuse IPs CrowdSec has banned (and inspect with AppSec)"
    )
```

In `RedirectionHostUpdate` after `block_exploits`, and in `DeadHostUpdate` after `hsts_subdomains`:

```python
    crowdsec_enabled: bool | None = None
```

In `backend/app/services/nginx/state.py`, add to `RedirectionHostSpec` (after `block_exploits`) and to `DeadHostSpec` (after `hsts_subdomains`):

```python
    crowdsec_enabled: bool = True
```

In `backend/app/services/nginx/loader.py`, add `crowdsec_enabled=r.crowdsec_enabled,` after `block_exploits=r.block_exploits,` in `_load_redirection_hosts`, and `crowdsec_enabled=d.crowdsec_enabled,` after `hsts_subdomains=d.hsts_subdomains,` in `_load_dead_hosts`.

- [ ] **Step 6: Run the tests**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_meg24_api.py tests/test_crowdsec_more_hosts_migration.py tests/test_meg24_render.py tests/test_meg24_engine.py`
Expected: all pass.

- [ ] **Step 7: Regenerate the contract**

```bash
docker exec -w /src megoopm-test python -m scripts.export_openapi
(cd frontend && npm run gen:api)
git diff --stat -- frontend/src/lib/api/generated/schema.ts   # expect a small diff, not thousands of lines
```

- [ ] **Step 8: Lint and commit**

```bash
docker exec -w /src megoopm-test ruff format app tests alembic
docker exec -w /src megoopm-test ruff check app tests alembic
git add backend frontend/src/lib/api/generated/schema.ts
git commit -m "feat(crowdsec): redirection and 404 hosts carry a CrowdSec switch, on by default"
```

---

### Task 4: Render the hook on redirection, 404, default-TLS and Force-SSL :80 servers

**Files:**
- Modify: `backend/app/templates/nginx/redirect.conf.j2`, `dead.conf.j2`, `default_tls.conf.j2`, `server.conf.j2`
- Modify: `backend/tests/test_meg24_render.py`, `backend/tests/test_nginx_render.py`, `backend/tests/test_certs_render.py` (only if an existing assertion breaks)

**Interfaces:**
- Consumes: `RedirectionHostSpec.crowdsec_enabled`, `DeadHostSpec.crowdsec_enabled` (Task 3).
- Produces: the rendered lines `server_rewrite_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;` and (proxy hosts, unchanged) `access_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;`.

- [ ] **Step 1: Write the failing render tests**

Append to `backend/tests/test_meg24_render.py`:

```python
HOOK = "server_rewrite_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;"


def test_redirect_enforces_bans_in_every_server_before_its_return() -> None:
    # `return` runs in the rewrite phase, before access: an access-phase hook
    # here would never run. The certificate + Force SSL shape has three servers.
    conf = render_config(
        DesiredState(redirection_hosts=(_redirect(certificate=_CERT, ssl_forced=True),))
    )["megoopm-redirect-1.conf"]
    assert conf.count("server {") == conf.count(HOOK) == 2
    assert "access_by_lua_file" not in conf


def test_redirect_without_crowdsec_renders_no_hook() -> None:
    conf = render_config(DesiredState(redirection_hosts=(_redirect(crowdsec_enabled=False),)))[
        "megoopm-redirect-1.conf"
    ]
    assert "megoopm_crowdsec" not in conf


def test_dead_host_enforces_bans_in_every_server() -> None:
    conf = render_config(DesiredState(dead_hosts=(_dead(certificate=_CERT),)))[
        "megoopm-dead-2.conf"
    ]
    assert conf.count("server {") == conf.count(HOOK) == 2


def test_dead_host_without_crowdsec_renders_no_hook() -> None:
    conf = render_config(DesiredState(dead_hosts=(_dead(crowdsec_enabled=False),)))[
        "megoopm-dead-2.conf"
    ]
    assert "megoopm_crowdsec" not in conf
```

If a redirection or dead host with a certificate renders three servers rather than two, change the expected `2` to the rendered `server {` count — the assertion that matters is that every server carries the hook.

In `backend/tests/test_nginx_render.py`, replace the body of `test_crowdsec_applies_to_tls_and_redirect_servers` after the `server = …` assignment with:

```python
    # The :443 server keeps the access-phase hook. The :80 server only redirects
    # to HTTPS, with a `return` that runs before access — so it uses the
    # server-rewrite hook, or a banned client would be redirected, not refused.
    assert server.count("access_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;") == 1
    assert server.count("server_rewrite_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;") == 1
```

and append:

```python
def test_a_proxy_host_without_force_ssl_keeps_the_access_hook_on_80() -> None:
    cert = CertificateSpec(
        id=7,
        fullchain_path="/etc/nginx/certs/7/fullchain.pem",
        privkey_path="/etc/nginx/certs/7/privkey.pem",
    )
    host = _host(certificate=cert, ssl_forced=False, crowdsec_enabled=True)
    server = render_config(DesiredState(proxy_hosts=(host,), http_upstreams=(_pool(),)))[
        "megoopm-proxy-1.conf"
    ]
    assert server.count("access_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;") == 2
    assert "server_rewrite_by_lua_file" not in server


def test_the_default_tls_site_always_enforces_bans() -> None:
    # Unclaimed names on a certificate draw scanners; there is nothing to opt out of.
    conf = render_config(DesiredState(default_tls=(_default_tls(),)))["megoopm-default-tls-3.conf"]
    assert "server_rewrite_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;" in conf
```

- [ ] **Step 2: Run them to see them fail**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_meg24_render.py tests/test_nginx_render.py`
Expected: the new tests and the rewritten one fail on missing `server_rewrite_by_lua_file`.

- [ ] **Step 3: Redirection and 404 templates**

At the top of `redirect.conf.j2` (after the header comment, before `redirect_location`) and of `dead.conf.j2` (before `dead_location`), add:

```jinja
{%- macro crowdsec() -%}
{%- if host.crowdsec_enabled %}
    # CrowdSec bouncer, in the server-rewrite phase: this server answers with
    # `return`, which runs before the access phase proxy hosts use.
    server_rewrite_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;
{%- endif %}
{%- endmacro -%}
```

and in each `server {}` block of both files, on the line after `    server_name {{ server_names }};`, add `{{- crowdsec() }}`.

- [ ] **Step 4: Default-TLS template**

In `default_tls.conf.j2`, after `    server_name {{ server_names }};`:

```jinja

    # CrowdSec bouncer, always on: unclaimed names on a certificate draw
    # scanners, and there is no host to opt out. Server-rewrite phase, like
    # every server that answers with `return`.
    server_rewrite_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;
```

- [ ] **Step 5: Proxy host Force-SSL :80 server**

In `server.conf.j2`, change the `crowdsec` macro to take the phase:

```jinja
{%- macro crowdsec(phase="access") -%}
{%- if host.crowdsec_enabled %}
    # CrowdSec bouncer (MEG-22): banned IPs are refused before the request
    # reaches the upstream. A server that only redirects to HTTPS answers with
    # `return`, which runs before the access phase, so it uses server_rewrite.
    # AppSec/WAF, when the engine is configured, is currently GLOBAL (applies to
    # every crowdsec-enabled host), so $megoopm_crowdsec_appsec is a reserved
    # marker the handler does not yet gate on — see docs/crowdsec.md.
    set $megoopm_crowdsec_appsec {% if host.crowdsec_appsec_enabled %}on{% else %}off{% endif %};
    {{ phase }}_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;
{%- endif %}
{%- endmacro -%}
```

and in the certificate branch's :80 server (the one followed by `{{- acme_challenge() }}` and `{%- if host.ssl_forced %}`), replace its `{{- crowdsec() }}` with:

```jinja
{{- crowdsec("server_rewrite" if host.ssl_forced else "access") }}
```

Leave the other two `{{- crowdsec() }}` calls as they are.

- [ ] **Step 6: Run the render tests**

Run: `docker exec -w /src megoopm-test python -m pytest tests/test_meg24_render.py tests/test_nginx_render.py tests/test_certs_render.py tests/test_maintenance_blocks.py`
Expected: all pass.

- [ ] **Step 7: Real `nginx -t` of every variant**

Write `/tmp` configs from the renderer and test them in the shipped image (the backend test container has no nginx binary):

```bash
docker exec -w /src megoopm-test python - <<'EOF'
import os, sys
sys.path.insert(0, "tests")
from test_meg24_render import _CERT, _dead, _redirect
from test_nginx_render import _default_tls, _host, _pool
from app.services.nginx import render_config
from app.services.nginx.state import CertificateSpec, DesiredState
os.makedirs("/src/.probe-conf", exist_ok=True)
state = DesiredState(
    proxy_hosts=(_host(certificate=_CERT, ssl_forced=True, crowdsec_enabled=True),),
    http_upstreams=(_pool(),),
    redirection_hosts=(_redirect(certificate=_CERT, ssl_forced=True),),
    dead_hosts=(_dead(certificate=_CERT),),
    default_tls=(_default_tls(),),
)
for name, content in render_config(state).items():
    open(f"/src/.probe-conf/{name}", "w").write(content)
EOF
```

Then run `nginx -t` in `megoopm-nginx` against `infra/nginx/nginx.conf` with `/data/nginx/conf.d` pointing at `backend/.probe-conf`, a self-signed pair at `/etc/nginx/certs/7/` and `/etc/nginx/certs/3/`, and the CrowdSec env vars set (as in Task 1). Expected: `test is successful`, no `[warn]` mentioning `server_rewrite_by_lua`. Delete `backend/.probe-conf` afterwards; never commit it.

- [ ] **Step 8: Commit**

```bash
docker exec -w /src megoopm-test ruff format tests && docker exec -w /src megoopm-test ruff check tests
git add backend/app/templates backend/tests
git commit -m "feat(nginx): enforce CrowdSec bans on redirection, 404, default-TLS and Force-SSL :80 servers"
```

---

### Task 5: The default :80 server enforces bans, `/healthz` exempt

**Files:**
- Modify: `infra/nginx/nginx.conf` (default server block)
- Create: `backend/tests/test_nginx_base_conf.py`
- Modify: `infra/nginx/tests/bouncer-phase.sh` (new case)

**Interfaces:**
- Consumes: `require("megoopm_crowdsec_check").check()` (Task 2).

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_nginx_base_conf.py`:

```python
"""The base nginx.conf's default server: bans enforced, healthcheck exempt.

Reads the file directly, like the compose tests, so it runs anywhere the repo
is checked out. The end-to-end proof is infra/nginx/tests/bouncer-phase.sh.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

NGINX_CONF = Path(__file__).resolve().parents[2] / "infra" / "nginx" / "nginx.conf"


def _default_server() -> str:
    if not NGINX_CONF.exists():  # pragma: no cover - partial checkout
        pytest.skip(f"{NGINX_CONF} not present")
    text = NGINX_CONF.read_text(encoding="utf-8")
    start = text.index("listen      80 default_server;")
    return text[start : text.index("include /data/nginx/default/*.conf;", start)]


def test_the_default_server_runs_the_bouncer_before_any_return() -> None:
    block = _default_server()
    assert "server_rewrite_by_lua_block" in block
    assert 'require("megoopm_crowdsec_check").check()' in block


def test_the_healthcheck_is_exempt() -> None:
    # AppSec fails closed; a CrowdSec outage must not mark nginx unhealthy.
    block = _default_server()
    assert re.search(r'if ngx\.var\.uri ~= "/healthz" then', block)
```

Append to `bouncer-phase.sh`, before case 6 (case 6 replaces the nginx container):

```bash
# 7. The default server (base nginx.conf, port 80) refuses a banned client
#    but keeps answering the healthcheck.
docker exec bp-crowdsec cscli decisions delete --ip "$CLIENT_IP" >/dev/null
docker exec bp-crowdsec cscli decisions add --ip "$CLIENT_IP" --duration 1h >/dev/null
wait_for_stream
expect "ban, default server"            403 "$(status 80 /)"
expect "ban, default server /healthz"   200 "$(status 80 /healthz)"
```

- [ ] **Step 2: Run them to see them fail**

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v C:/Projects/megoopm:/repo -w /repo/backend --entrypoint sh megoopm-backend \
  -c 'pip install -q pytest >/dev/null 2>&1; python -m pytest tests/test_nginx_base_conf.py --noconftest -p no:cacheprovider -o addopts=""'
MSYS_NO_PATHCONV=1 bash infra/nginx/tests/bouncer-phase.sh
```

Expected: both pytest tests fail; harness case 7 `FAIL  ban, default server: expected 403, got 404`.

- [ ] **Step 3: Add the hook to the default server**

In `infra/nginx/nginx.conf`, inside the `listen 80 default_server;` server, after the `root /var/empty/megoopm;` line and its comment:

```nginx

        # CrowdSec bouncer for requests no host claims — almost all scanners.
        # Server-rewrite phase: the default site answers with `return`, which
        # runs before the access phase. /healthz is exempt: AppSec fails closed,
        # and a CrowdSec outage must not get nginx marked unhealthy and restarted.
        server_rewrite_by_lua_block {
            if ngx.var.uri ~= "/healthz" then
                require("megoopm_crowdsec_check").check()
            end
        }
```

- [ ] **Step 4: Run both again**

Same commands as Step 2. Expected: pytest 2 passed; harness `PASS`.

- [ ] **Step 5: Commit**

```bash
sed -i 's/\r$//' infra/nginx/nginx.conf backend/tests/test_nginx_base_conf.py infra/nginx/tests/bouncer-phase.sh
git ls-files --eol infra/nginx/nginx.conf   # must show w/lf
git add infra/nginx/nginx.conf backend/tests/test_nginx_base_conf.py infra/nginx/tests/bouncer-phase.sh
git commit -m "feat(nginx): the default server refuses banned IPs, healthcheck exempt"
```

---

### Task 6: The switch in the redirection and 404 host dialogs

**Files:**
- Modify: `frontend/src/components/redirection-hosts/redirection-host-dialog.tsx`, `.test.tsx`
- Modify: `frontend/src/components/dead-hosts/dead-host-dialog.tsx`, `.test.tsx`
- Modify: `frontend/src/components/settings/settings-view.tsx` (one sentence)

**Interfaces:**
- Consumes: `crowdsec_enabled` on `RedirectionHost`/`DeadHost` and their create/update payloads (Task 3's generated types).

Description text, used verbatim in both dialogs:
`Refuse IPs CrowdSec has banned (and inspect requests with the AppSec WAF). While CrowdSec is unreachable this host refuses traffic.`

- [ ] **Step 1: Write the failing dialog tests**

In `dead-host-dialog.test.tsx`, add `crowdsec_enabled: true,` to `makeDeadHost`'s defaults, then add inside the `describe`:

```tsx
  it("offers CrowdSec protection on Details, on for a new host", () => {
    renderDialog(null);
    expect(screen.getByLabelText("CrowdSec protection")).toHaveAttribute("aria-checked", "true");
  });

  it("sends the CrowdSec switch when saving", async () => {
    const user = userEvent.setup();
    renderDialog(makeDeadHost({ crowdsec_enabled: true }));

    await user.click(screen.getByLabelText("CrowdSec protection"));
    await user.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(deadHosts.update).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ crowdsec_enabled: false }),
      ),
    );
  });
```

Add the same two tests to `redirection-host-dialog.test.tsx`, using that file's own host factory (add `crowdsec_enabled: true` to it), `redirectionHosts.update`, and its render helper. If the Switch component exposes state as `data-checked` or `aria-pressed` rather than `aria-checked`, match the assertion to the existing toggle tests in the same file.

- [ ] **Step 2: Run them to see them fail**

Run: `cd frontend && npx vitest run src/components/dead-hosts src/components/redirection-hosts`
Expected: the new tests fail — no element labelled "CrowdSec protection".

- [ ] **Step 3: Redirection dialog**

In `redirection-host-dialog.tsx`:
- add to `DETAILS_TOGGLES`:
  `["crowdsec_enabled", "CrowdSec protection", "Refuse IPs CrowdSec has banned (and inspect requests with the AppSec WAF). While CrowdSec is unreachable this host refuses traffic."],`
- add `| "crowdsec_enabled"` to `ToggleKey`;
- `emptyToggles()`: `crowdsec_enabled: true,`;
- `stateFromHost` toggles: `crowdsec_enabled: host.crowdsec_enabled,`.

The submit path already spreads `...form.toggles`.

- [ ] **Step 4: 404 host dialog**

In `dead-host-dialog.tsx`:
- after `TLS_TOGGLES`, add
  ```tsx
  /** Security options that apply with or without TLS — these stay on Details. */
  const DETAILS_TOGGLES = [
    [
      "crowdsec_enabled",
      "CrowdSec protection",
      "Refuse IPs CrowdSec has banned (and inspect requests with the AppSec WAF). While CrowdSec is unreachable this host refuses traffic.",
    ],
  ] as const;
  ```
- change `ToggleKey` to `(typeof TLS_TOGGLES)[number][0] | (typeof DETAILS_TOGGLES)[number][0]`;
- `emptyToggles()`: `crowdsec_enabled: true,`; `stateFromHost` toggles: `crowdsec_enabled: host.crowdsec_enabled,`;
- in the Details panel, after the `Enabled` `ToggleRow`, render the list exactly as the redirection dialog renders its `DETAILS_TOGGLES` (copy that `.map` block, adjusting nothing but the ids' prefix to `dead-`).

- [ ] **Step 5: Default site note**

In `settings-view.tsx`, change the Default site description paragraph to:

```tsx
          <p className="text-sm text-muted-foreground">
            What to serve for a request that matches no configured host. IPs CrowdSec has banned
            are refused here, whatever you choose.
          </p>
```

- [ ] **Step 6: Run the frontend gate**

```bash
cd frontend
npx vitest run
npx tsc --noEmit
npx eslint src/components/dead-hosts src/components/redirection-hosts src/components/settings
# prettier: touched files only, at the width their HEAD version is clean at
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components
git commit -m "feat(ui): CrowdSec protection switch on redirection and 404 hosts"
```

---

### Task 7: Documentation and the full gate

**Files:**
- Modify: `docs/crowdsec.md` (Moving parts table row; "Request enforcement flow")

- [ ] **Step 1: Update the docs**

- In the "Moving parts" table, the per-host toggles row becomes: `proxy_hosts.crowdsec_enabled`, `redirection_hosts.crowdsec_enabled`, `dead_hosts.crowdsec_enabled` (bouncer, per host; the latter two default on) / `crowdsec_appsec_enabled` (reserved).
- Replace "Request enforcement flow" step 1 with the coverage table from the spec's Problem section, updated to the new state (every row "yes"; redirection/404 "when switched on"), plus one paragraph on why servers answering with `return` use `server_rewrite_by_lua_file`, and one on the fail-closed consequence of AppSec for those servers.
- Add: "`megoopm_crowdsec_check.lua` holds the check; `megoopm_crowdsec.lua` and the base default server call it."

- [ ] **Step 2: Full gate**

```bash
MSYS_NO_PATHCONV=1 docker exec -w /src megoopm-test python -m pytest            # full backend suite
MSYS_NO_PATHCONV=1 docker exec -w /src megoopm-test ruff check app tests alembic
(cd frontend && npx vitest run && npx tsc --noEmit)
MSYS_NO_PATHCONV=1 bash infra/nginx/tests/bouncer-phase.sh                         # end to end
```

Expected: all green, harness `PASS`.

- [ ] **Step 3: Commit**

```bash
git add docs/crowdsec.md
git commit -m "docs(crowdsec): where bans are enforced, and why some servers use server_rewrite"
```
