# CrowdSec integration (MEG-22)

MegooPM ships a built-in [CrowdSec](https://crowdsec.net) security engine: an
IP-reputation **bouncer** and an inline **AppSec/WAF**, both wired into the
managed nginx proxy and toggleable **per proxy host**, plus a backend that talks
to the CrowdSec Local API (LAPI).

## Alert fetch cap

`GET /crowdsec/alerts` pulls up to `CROWDSEC_ALERT_FETCH_CAP` (default **200**)
alerts from LAPI before filtering and paginating, so `total` is relative to that
window.

The default is low on purpose. CrowdSec 1.6.4 **hangs** on `GET /v1/alerts` with
a large limit — measured against a live LAPI holding ~136 alerts, `limit=200`
returned every one of them in 0.03s while `limit=1000` timed out on 4 of 4
attempts. The previous cap of 1000 fetched no extra data; it only triggered the
hang, which reached operators as a bare 503 on the Security page.

Raise it if you need a wider window, but measure first:

```bash
docker compose exec backend python -c "
import asyncio, time, httpx
from app.db.session import SessionLocal
from app.services.crowdsec import credentials
async def main():
    async with SessionLocal() as db:
        s = await credentials.resolve_settings(db)
    async with httpx.AsyncClient(base_url=s.crowdsec_lapi_url, timeout=30) as c:
        r = await c.post('/v1/watchers/login', json={
            'machine_id': s.crowdsec_machine_id, 'password': s.crowdsec_machine_password})
        h = {'Authorization': 'Bearer ' + r.json()['token']}
        for n in (200, 500, 1000):
            t = time.monotonic()
            try:
                rr = await c.get('/v1/alerts', headers=h, params={'limit': n})
                print(n, 'OK', round(time.monotonic()-t, 2), 's', len(rr.content), 'bytes')
            except Exception as e:
                print(n, 'FAILED', type(e).__name__)
asyncio.run(main())"
```

## Moving parts

| Piece | Where | Role |
| --- | --- | --- |
| CrowdSec engine | `crowdsec` service (compose) | LAPI on `:8080`, AppSec on `:7422`, detects & decides |
| nginx bouncer + AppSec | `infra/nginx/` (OpenResty image) | Enforces decisions / forwards to AppSec in nginx's access phase |
| Per-host toggles | `proxy_hosts.crowdsec_enabled` (bouncer, per host) / `crowdsec_appsec_enabled` (reserved — AppSec is global, see below) | Which hosts are protected |
| Rendered directives | `backend/app/templates/nginx/server.conf.j2` | Emits the bouncer handler into a host's server block |
| Backend LAPI client | `app/services/crowdsec/` + `app/api/routes/crowdsec.py` | Read decisions/alerts, push manual decisions |

## Request enforcement flow

1. The backend renders `access_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;`
   (and `set $megoopm_crowdsec_appsec on|off;`) into the server block of every
   host with `crowdsec_enabled=true`.
2. `megoopm_crowdsec_init.lua` initialises the CrowdSec bouncer module once at
   nginx startup from `crowdsec-bouncer.conf` (rendered from the environment).
3. On each request to a protected host, `megoopm_crowdsec.lua` calls the stock
   bouncer's `Allow()`, which:
   - applies any active IP decision (ban/captcha/throttle) → the request is
     terminated at the edge; and
   - if the AppSec engine is configured, forwards the request to the AppSec
     component, which blocks malicious payloads before they reach the upstream.

Bouncer enforcement is **per host**: hosts with `crowdsec_enabled` off never
reference the Lua handler, so they are untouched. The switch is
**CrowdSec protection** on the proxy-host dialog's *Advanced* tab.

### Where decisions come from

| Source | How | Enabled by |
| --- | --- | --- |
| Manual bans | Security page → **Ban**, or `cscli decisions add` | always |
| AppSec / inline WAF | malicious request payloads on protected hosts | `CROWDSEC_APPSEC_URL` (global, see below) |
| nginx log scenarios (`crowdsecurity/nginx`: probing, bad user agents, sensitive files, brute force, …) | the managed nginx ships access + error logs to the CrowdSec agent over **syslog/UDP** (`access_log syslog:server=$CROWDSEC_SYSLOG_ADDR,tag=nginx`, rendered by `infra/nginx/docker-entrypoint.sh`); `infra/crowdsec/acquis/nginx-syslog.yaml` listens on `:514/udp`; the syslog parser sets `program=nginx` so the nginx parsers/scenarios apply | `CROWDSEC_SYSLOG_ADDR` (set in every compose file; per node in HA) |
| Community blocklist (CAPI) | CrowdSec's shared threat intel | off by default; switch it on in **Security → Updates** (registers with CAPI and restarts CrowdSec — see *Updates tab* below) |

**Client IP behind a CDN / tunnel / load balancer.** nginx sees the proxy's
address unless `NGINX_REAL_IP_HEADER` (e.g. `CF-Connecting-IP`) and
`NGINX_REAL_IP_FROM` (trusted proxy CIDRs) are set; the entrypoint renders them
as `real_ip_header` / `set_real_ip_from`. With them, both the logs and the
bouncer's `Allow()` see the real client. **Without them, log-based detections
would eventually ban the proxy itself** — i.e. all traffic.

### AppSec scope: global, not per host (yet)

AppSec/WAF is currently a **global on/off**, not per host. lua-cs-bouncer
v1.0.8 runs AppSec *inside* `Allow()` whenever `APPSEC_URL` is set in
`crowdsec-bouncer.conf`, with no per-request switch. So once the AppSec engine
is wired, **every** `crowdsec_enabled` host is inspected, regardless of its
per-host `crowdsec_appsec_enabled` value.

- The per-host `crowdsec_appsec_enabled` flag (and the `$megoopm_crowdsec_appsec`
  variable rendered into each server block) is **retained but not yet enforced**
  — a reserved marker so genuine per-host AppSec can be reintroduced later
  without an API/schema change.
- To turn AppSec **off** entirely, leave `CROWDSEC_APPSEC_URL` blank so
  `APPSEC_URL` renders empty; the bouncer then does IP remediation only.

This was a deliberate scope call on MEG-32/D3: making AppSec genuinely per-host
requires reimplementing `Allow()`'s remediation handling against the module's
non-public internals, which was judged not worth blocking the CrowdSec chain on.
Reintroduce per-host AppSec when a user asks for it.

## Backend API (`/api/v1/crowdsec`, admin-only)

| Method | Path | Purpose | Auth needed |
| --- | --- | --- | --- |
| GET | `/crowdsec/health` | Is LAPI configured & reachable? | none (reports state) |
| GET | `/crowdsec/decisions` | List active decisions | bouncer key |
| GET | `/crowdsec/alerts` | List recent alerts | machine creds |
| POST | `/crowdsec/decisions` | Push a manual ban (audited) | machine creds |
| DELETE | `/crowdsec/decisions/{id}` | Lift a decision (audited) | machine creds |

Missing credentials return **503** (integration optional per deployment);
upstream LAPI failures return **502**. Manual decisions/removals are written to
the audit log (`object_type=crowdsec_decision`).

### Pagination & the community filter (MEG-43)

`GET /crowdsec/decisions` and `GET /crowdsec/alerts` are paginated and hide
community/blocklist records by default:

- `page` (1-based, default `1`) and `page_size` (default `50`, **max `200`** —
  a larger value is rejected `422`). Responses carry `total` / `page` /
  `page_size` alongside `items`.
- `include_community` (`bool`, default `false`). By default the list returns
  only **local** records — manual/operator bans (`origin=megoopm`), engine
  scenario hits (`origin=crowdsec`), AppSec/WAF detections (decision-less
  alerts). Set `include_community=true` to also include community-sourced
  records: `origin ∈ {CAPI, lists, cscli-import, community-blocklist}` (matched
  case-insensitively; confirm the exact live strings against your LAPI). An
  alert is "community" iff **any** of its decisions has a community origin, so
  decision-less AppSec alerts always stay in the local view.

Filtering and pagination are applied **server-side** after fetching from LAPI
(which has no reliable total/offset contract for these endpoints), so `total`
reflects the filtered set. Alerts are fetched from LAPI up to a bounded window
(`ALERT_FETCH_CAP`, 1000) before pagination, so on very large histories `total`
is relative to that window rather than the entire alert table — this keeps a
single response bounded in memory/latency.

## DB-backed credentials & auto-registration (MEG-43)

Credentials no longer have to come from the environment. They live in the
singleton `crowdsec_credentials` table (`app/models/crowdsec.py`), and the
backend **self-registers** on a fresh stack so no manual `cscli`/env step is
required.

**Encryption at rest.** `machine_password` and `bouncer_key` are stored as
Fernet ciphertext (`app/core/crypto.py`), keyed off the app `secret_key` (the
same root secret that signs JWTs — no extra key to manage). `lapi_url` /
`machine_id` are stored in the clear. Rotating `secret_key` invalidates existing
ciphertext by design; re-register or re-seed to repopulate it.

**Resolution order** (`app/services/crowdsec/credentials.py`, cached in-process,
cache invalidated on any write):

1. If the DB row exists → decrypt and use it.
2. Else if `CROWDSEC_*` env vars are set → **seed** them into the DB once
   (env→DB bootstrap) and use them.
3. Else → unconfigured (health reports it; routes 503, never 500).

**Registration mechanism** (`app/services/crowdsec/registration.py`,
`ensure_registered()` — run best-effort at app startup **and** by the
request-scoped client dependency, cached and idempotent):

- **Machine:** whenever the resolved credentials have no machine — no row at
  all, or a row seeded from the environment with only the bouncer key (which is
  what every compose stack passes) — the backend self-registers one over LAPI
  HTTP via `POST /v1/watchers` with a generated `machine_id`
  (`{origin}-{random}`) and a strong random password, keeping the existing
  bouncer key, then persists it encrypted. Registration is idempotent (an
  already-existing machine → LAPI `403` → treated as success).
  - **Auto-validation:** the request carries `registration_token` =
    `CROWDSEC_REGISTRATION_TOKEN`. The stacks mount
    `infra/crowdsec/config.yaml.local`, which enables LAPI's
    `api.server.auto_registration` with the same token (expanded from the
    CrowdSec container's environment) and private `allowed_ranges`, so the
    machine is validated on the spot — no `cscli machines validate`. Without a
    token the machine still registers but stays pending until an operator
    validates it. The health endpoint reports `machine_registered`, and the
    Security page shows a warning while it is false.
  - A seeded `CROWDSEC_MACHINE_ID`/`PASSWORD` must belong to **this** LAPI: a
    machine created on another CrowdSec instance fails login with
    `machine not found` (HTTP 401 → API 503).
- **Bouncer key:** CrowdSec exposes **no** LAPI HTTP endpoint to mint a bouncer
  key (`cscli bouncers add` writes the engine DB directly). So the bouncer key
  is **optional** for the backend: it is used for the decision-read path when
  present (sourced from `CROWDSEC_BOUNCER_KEY` env and seeded to the DB), and
  when absent the backend reads decisions with the **machine token** instead.
  The edge nginx bouncer keeps its own key, provisioned by the stack
  (`BOUNCER_KEY_megoopm`) as before — that is unchanged by this work.

**Concurrency safety.** `ensure_registered()` takes a Postgres transaction
advisory lock (`pg_advisory_xact_lock`, no-op on SQLite/single-host) around the
register-then-persist critical section, and the singleton primary key is the
ultimate backstop: if two workers still race, the PK collision is caught and the
loser reads back the winner's row instead of double-registering.

## Configuration

Since MEG-43 the backend **auto-registers** its machine on first startup, so a
fresh stack needs **no** manual `cscli machines add` and no `CROWDSEC_MACHINE_*`
env vars. The env vars below remain supported as a **bootstrap seed** — when set
on a stack whose DB is still empty, they are migrated into the encrypted
`crowdsec_credentials` table once; thereafter the DB is the source of truth.

Environment (see `.env.example`, all optional):

- `CROWDSEC_LAPI_URL` — LAPI base URL (default `http://crowdsec:8080`).
- `CROWDSEC_BOUNCER_KEY` — bouncer API key; drives the nginx bouncer **and**
  (when set) the backend decision-read path. CrowdSec auto-registers the nginx
  bouncer via `BOUNCER_KEY_megoopm`. Optional for the backend — it falls back to
  the machine token to read decisions.
- `CROWDSEC_REGISTRATION_TOKEN` — LAPI auto-registration token (>= 32 chars),
  passed to **both** the CrowdSec container (where
  `infra/crowdsec/config.yaml.local` expands it into
  `api.server.auto_registration.token`) and the backend (sent as
  `registration_token` when self-registering), so the machine is validated
  without `cscli`. Required in the production compose files; defaulted in dev.
- `CROWDSEC_MACHINE_ID` / `CROWDSEC_MACHINE_PASSWORD` — **optional** override; if
  set they seed the DB, otherwise the backend self-registers its own machine.
  Only for a machine created on *this* LAPI.

## Whitelists (UI-authored)

The Security page has a **Whitelists** tab where an operator names a set of IPs
and CIDR ranges that CrowdSec should ignore — the answer to an internal backend
tripping an AppSec rule and getting banned.

A whitelist is one of two **kinds**:

- **IP / CIDR** — exempt specific addresses or ranges. Fully validated before
  anything is written: a bad address is a 422 and never reaches disk.
- **Expression** — a CrowdSec `expr` expression, with an optional top-level
  `filter:` scoping which events it runs against. Use it for false positives
  that are not about *who* is calling but *what* they called, e.g. a health
  check tripping an AppSec rule.

These are **parser whitelists**: the event is dropped in the parsing pipeline,
so no alert and no decision is ever created. That is a deliberate choice over
suppressing enforcement in the nginx bouncer, which would leave the Security
page showing bans that are not being enforced.

### How a whitelist reaches CrowdSec

1. Rows in `crowdsec_whitelists` render into **one multi-document YAML file** at
   `/data/crowdsec/whitelists/megoopm.yaml` (`CROWDSEC_WHITELIST_PATH`), ordered
   by id so the output is byte-stable.
2. The CrowdSec container reads it through a **single-file** bind mount at
   `/etc/crowdsec/parsers/s02-enrich/99-megoopm-whitelist.yaml`.
3. A Celery task on the control-plane node restarts the container so it
   re-reads its parsers, then polls LAPI until it answers.

CrowdSec has no reload endpoint and LAPI exposes no route for parser
configuration, so restarting the container is the only channel available. That
is why the socket is involved at all.

### Things that will bite you

**The mount is a file, not a directory.** `s02-enrich/` already contains
hub-installed parsers — verified against v1.6.4, it holds `geoip-enrich.yaml`,
`dateparse-enrich.yaml` and `whitelists.yaml` as symlinks into
`/etc/crowdsec/hub/`. Mounting a directory over it would mask all of them and
silently break enrichment.

**The file must exist before the container starts.** Docker creates a
*directory* when a bind-mount source is missing, and CrowdSec then dies trying
to parse it. `data-init` seeds a placeholder for exactly this reason; that seed
is also what makes whitelists load at boot.

Seeding is only half of it — the **ordering** is the other half. `crowdsec`
declares `depends_on: data-init` so it cannot be created before the file
exists. Without that they race, and Docker wins:

```
data-init-1  | sh: can't create /data/crowdsec/whitelists/megoopm.yaml: Is a directory
data-init-1  | data-init: cannot prepare /data (SHARED_DATA_PATH) for uid 1000
```

If a stack ever reaches that state, the bogus path is an empty directory:

```bash
docker compose -f docker-compose.ha.yml down
rmdir "$SHARED_DATA_PATH/crowdsec/whitelists/megoopm.yaml"
docker compose -f docker-compose.ha.yml up -d
```

**The file is written in place, never replaced.** The container resolves the
mount to an inode when it starts, so a write-temp-then-rename would leave it
reading the old content for the rest of its life with no error in any log. See
the comment on `write_whitelist_file` — this is the opposite of the atomic-write
habit that is correct everywhere else in this codebase, and there is a test
asserting the inode does not change.

**A malformed file takes the whole edge down.** Measured on v1.6.4: a broken
whitelist fails at *hub index scan*, before parsers even load —
`failed to read Hub index: ... failed to parse ... yaml: line 3` — and CrowdSec
never comes up. AppSec is then unreachable and the bouncer runs
`APPSEC_FAILURE_ACTION=deny`, so every `crowdsec_enabled` host denies every
request until someone intervenes.

That is why the apply validates before writing, keeps the previous bytes, and
**restores them and restarts again** if LAPI does not answer within
`CROWDSEC_RELOAD_HEALTH_TIMEOUT_SECONDS`. Rollback is part of the feature.

**An expression cannot be validated before you save it.** CrowdSec compiles
`expr` itself and there is no offline compiler to call from the backend, so the
first time a typo is caught is when CrowdSec refuses to start. Measured on
v1.6.4:

```
level=fatal msg="crowdsec init: while loading parsers: failed to compile node
'megoopm/wl-broken-expr' in '/etc/crowdsec/parsers/s02-enrich/99-megoopm-whitelist.yaml'
: unable to compile whitelist expression '...' : unexpected token Operator("==") (1:22)"
```

The rollback catches it — LAPI never answers, the previous file goes back — so
the cost is one restart cycle, not an outage. But it *is* a real restart cycle
triggered from a form, which is why the dialog says so plainly. IP/CIDR
whitelists carry no such risk.

**Applying restarts CrowdSec, which briefly denies traffic.** For the few
seconds the container is down, AppSec is unreachable and the bouncer fails
closed on every protected host. This is why an unchanged render performs no
restart at all: the apply compares both the content digest and the bytes on
disk, and does nothing when neither has moved.

### Multi-document rendering

One file holds one YAML document per whitelist, `---` separated, regardless of
kind. Verified on v1.6.4: a file with one IP/CIDR and two expression whitelists
logs `Loaded 3 parser nodes`, so all of them are live. Note that `cscli parsers list` shows only **one row per file**,
naming the first document — that is a display detail, not a sign the rest were
ignored.

Names render as `megoopm/wl-<slug>`. CrowdSec requires parser names to be
unique across everything it loads, so the API returns 409 when two names
slugify to the same value rather than letting the container fail to start.

Each document renders only its own kind's keys. That is not cosmetic: CrowdSec
evaluates every key it finds, so an `ip:` list left on an expression whitelist
would silently widen it. The API rejects a payload carrying the other kind's
fields rather than dropping them.

Scalars are emitted as JSON strings (JSON being a subset of YAML), so a reason
or an expression containing `:` or `#` cannot break the file. Note this uses a
local `yamlstr` filter, **not** Jinja's `tojson` — that one is HTML-safe and
escapes every `'` and `&` into `'` / `&`, which YAML decodes back
correctly but renders an expression unreadable in the dialog's preview.

### Configuration

- `CROWDSEC_CONTROL_NODE_ID` — **HA only.** The node whose worker holds the
  docker socket and runs the CrowdSec container. Set it to that node's
  `NODE_ID` and use the **same value on every node**: it names the control
  plane, it is not each node's own id. Leave blank to disable reloads —
  whitelists then save but the Security page reports them as not applied,
  rather than implying they are in force.

  On a single-node stack (`HA_ENABLED=false`) leave it blank. Workers only
  consume a `megoopm.node.<id>` queue when HA is on, so the apply goes to the
  default queue that the one worker already consumes; addressing a node queue
  there would leave the task unconsumed forever.
- `CROWDSEC_CONTAINER_NAME` — container the reload restarts (default
  `megoopm-crowdsec-1`). Check with `docker ps --format '{{.Names}}'`.
- `CROWDSEC_WHITELIST_PATH` — rendered file (default
  `/data/crowdsec/whitelists/megoopm.yaml`).
- `CROWDSEC_RELOAD_HEALTH_TIMEOUT_SECONDS` — how long to wait for LAPI after a
  restart before rolling back (default 60).
- `DOCKER_SOCKET_PATH` — default `/var/run/docker.sock`.
- `CROWDSEC_CONFIG_LOCAL_PATH` — the app-owned `config.yaml.local` (default
  `/data/crowdsec/config.yaml.local`); see *Updates tab* below.

The socket is mounted on the **worker** service only, never on `backend`. It is
root-equivalent on the host, and `backend` is the process taking internet
traffic.

### Verifying on a live stack

```bash
# The file CrowdSec actually sees
docker compose exec crowdsec cat /etc/crowdsec/parsers/s02-enrich/99-megoopm-whitelist.yaml

# One row per file, named after the first document
docker compose exec crowdsec cscli parsers list | grep megoopm

# The authoritative check — how many nodes the engine loaded
docker compose logs crowdsec | grep "99-megoopm-whitelist"
```

Then save a whitelist unchanged and confirm the container's uptime does **not**
reset (`docker ps`): a no-op save must not restart CrowdSec.

## Updates tab (hub refresh + community blocklist)

Security → **Updates** owns two things the image does not do on its own.

### Hub refresh

CrowdSec's parsers, scenarios, collections and AppSec rules come from its hub,
and the official image refreshes them **only when the container starts**
(`cscli hub update` if the index is older than 24 h, then `cscli hub
upgrade`). A container that stays up for a month has month-old
virtual-patching rules.

- **Schedule.** Beat fires `app.tasks.crowdsec.hub_update_tick` at five past
  every hour. The tick reads the settings (`crowdsec_hub_auto_update`,
  `crowdsec_hub_update_frequency` daily/weekly, `crowdsec_hub_update_weekday`
  Monday = 0, `crowdsec_hub_update_hour_utc`) and runs the job when the hour
  (and, weekly, the weekday) match and it has not already run this hour.
  Defaults: on, daily, 03:00 UTC. The UI picks the hour in browser-local time.
- **The job** (`app.tasks.crowdsec.update_hub`, also **Update now**) runs in
  the container over the docker socket: `cscli hub list -o json`, a tarball
  of the hub and item directories to `/var/lib/crowdsec/data/megoopm-hub-backup.tgz`,
  `cscli hub update`, `cscli hub upgrade`, `cscli hub list` again. **Nothing
  changed → no restart.** Something changed → restart, wait for LAPI; if it
  does not come back, untar the backup *over* the item directories (the
  whitelist file is a bind mount and cannot be deleted) and restart again.
  Rollback is best effort: it restores every file that existed, not files the
  upgrade added.
- **Outcome** lives in `crowdsec_job_run` (`kind=hub_update`) and shows on
  the tab: when, which items changed, whether CrowdSec restarted, the agent
  version, and the hub's "newer agent available" warning. The image is
  pinned; items that need a newer agent are skipped silently by `cscli`, so
  bump the image when the tab says a newer release exists.
- A Redis lock (`megoopm:crowdsec:hub-update`) stops the schedule and Update
  now from overlapping; Update now is 409 while a run is open.

### Community blocklist

`DISABLE_ONLINE_API=true` stays in every compose file. The switch works by
having the app own `config.yaml.local`, which CrowdSec merges over
`config.yaml` at load time — *after* the entrypoint has deleted
`online_client`. `app/services/crowdsec/capi.py` renders the file:

- always the `auto_registration` block (the same text as
  `infra/crowdsec/config.yaml.local`, which `data-init` uses to seed the
  file — **keep the two in step**);
- plus, when on, an `online_client` block pointing at
  `/etc/crowdsec/online_api_credentials.yaml` with `sharing: true` and
  `pull.community/blocklists: true`.

Turning it on (`app.tasks.crowdsec.apply_capi`): write the file, run `cscli
capi register -f …` if no credentials exist yet (the override must already be
on disk — `cscli` refuses otherwise), restart, wait for LAPI, and require
`cscli capi status` to say "You can successfully interact". Any failure puts
the previous file back and restarts. Never reference a credentials file that
does not exist: CrowdSec then fails to start. Turning off removes the block
and restarts. Credentials persist in the `crowdsec_config` volume, so
re-enabling does not register a second identity. Once on, the agent pulls
the blocklist every two hours by itself.

`crowdsec_capi_enabled` is the **desired** state; `crowdsec_job_run
(kind=capi_apply)` is what was achieved. The tab shows both when they differ.

### Wiring

- `config.yaml.local` is a single-FILE mount out of the data path, seeded by
  `data-init` from the repo template (mounted read-only at `/seed`):
  production and dev use an `app_data` volume `subpath` (Docker Engine 26+),
  HA binds `${SHARED_DATA_PATH}/crowdsec/config.yaml.local`.
- The worker needs the docker socket and the data path in every stack (dev
  gained both with this feature).
- `USE_WAL: "true"` on CrowdSec: inserting the blocklist into a non-WAL
  SQLite can stall LAPI.
- Under HA both tasks are routed to `CROWDSEC_CONTROL_NODE_ID`'s queue; unset,
  the API refuses with 409 and the tab explains why.

### Verifying on a live stack

```bash
docker compose exec crowdsec cscli hub list          # versions after a run
docker compose exec crowdsec cscli capi status       # "successfully interact" when on
docker compose exec crowdsec cat /etc/crowdsec/config.yaml.local
docker ps --format '{{.Names}} {{.Status}}' | grep crowdsec   # uptime unchanged after an idle run
```

## Verifying (QA / live stack)

The Lua enforcement and image build require the full stack; they cannot be
exercised by the backend unit tests (which cover rendering + the LAPI client
against a mock transport). To verify the acceptance criteria end-to-end:

1. `docker compose up --build` and create a proxy host with `crowdsec_enabled`
   (and optionally `crowdsec_appsec_enabled`).
2. **Ban blocks:** `docker compose exec crowdsec cscli decisions add --ip <your-ip> --duration 5m`
   then request the host → expect `403` from the bouncer, carrying the ban page
   chosen under Settings (the MegooPM page by default). A *bare* 403 with no
   document means either the `none` mode is selected or the template failed to
   load — check the nginx log for `BAN_TEMPLATE_PATH and REDIRECT_LOCATION
   variable are empty`, and that `megoopm-ban.html` exists in the shared
   default directory.
3. **AppSec blocks:** with AppSec on, send a known-malicious payload
   (e.g. `?x=/etc/passwd` style probe covered by the generic rules) → expect a
   block.
4. **Backend reads:** `GET /api/v1/crowdsec/decisions` (admin) lists the ban;
   with machine creds set, `GET /crowdsec/alerts` lists detections.
5. **Manual decision:** `POST /api/v1/crowdsec/decisions {"value":"1.2.3.4"}`
   → the ban appears in `cscli decisions list` and the audit log.

### MEG-43 acceptance (DB creds + auto-registration + pagination/filter)

On a **fresh stack with no `CROWDSEC_MACHINE_*` env** and no manual `cscli`:

6. **Auto-registration:** `docker compose up` the backend, then
   `docker compose exec crowdsec cscli machines list` → a `megoopm-<random>`
   machine appears. Confirm the DB row: `SELECT machine_id, registered_at,
   machine_password_enc FROM crowdsec_credentials;` — one row, and
   `machine_password_enc` is **ciphertext, not plaintext**. Restart the backend
   → still one row (idempotent, no duplicate machine).
   - If the machine shows as **not validated**, run
     `cscli machines validate megoopm-<random>` once (see the auto-validation
     caveat above) and re-check that alerts/decisions read succeeds.
7. **Pagination:** with many decisions/alerts present,
   `GET /crowdsec/decisions?page=1&page_size=10` returns `total` +
   `page`/`page_size` and a bounded `items`; a large list does not 500/time out.
   `page_size=1000` → `422`.
8. **Community filter:** default `GET /crowdsec/decisions` hides CAPI/blocklist
   records; `?include_community=true` includes them and raises `total`. Confirm
   the live origin strings match `{CAPI, lists, cscli-import,
   community-blocklist}` (adjust the set in `filtering.py` if the live LAPI
   differs).
9. **Ban/unban still work** end-to-end reading the DB creds (no env), and are
   audited as before.
