# CrowdSec integration (MEG-22)

MegooPM ships a built-in [CrowdSec](https://crowdsec.net) security engine: an
IP-reputation **bouncer** and an inline **AppSec/WAF**, both wired into the
managed nginx proxy and toggleable **per proxy host**, plus a backend that talks
to the CrowdSec Local API (LAPI).

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
| Community blocklist (CAPI) | CrowdSec's shared threat intel | off (`DISABLE_ONLINE_API=true`) — enable and `cscli capi register` if wanted |

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

## Verifying (QA / live stack)

The Lua enforcement and image build require the full stack; they cannot be
exercised by the backend unit tests (which cover rendering + the LAPI client
against a mock transport). To verify the acceptance criteria end-to-end:

1. `docker compose up --build` and create a proxy host with `crowdsec_enabled`
   (and optionally `crowdsec_appsec_enabled`).
2. **Ban blocks:** `docker compose exec crowdsec cscli decisions add --ip <your-ip> --duration 5m`
   then request the host → expect `403` from the bouncer.
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
