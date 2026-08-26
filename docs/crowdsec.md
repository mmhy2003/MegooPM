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
| Per-host toggles | `proxy_hosts.crowdsec_enabled` / `crowdsec_appsec_enabled` | Which hosts are protected |
| Rendered directives | `backend/app/templates/nginx/server.conf.j2` | Emits the bouncer handler into a host's server block |
| Backend LAPI client | `app/services/crowdsec/` + `app/api/routes/crowdsec.py` | Read decisions/alerts, push manual decisions |

## Request enforcement flow

1. The backend renders `access_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;`
   (and `set $megoopm_crowdsec_appsec on|off;`) into the server block of every
   host with `crowdsec_enabled=true`.
2. `megoopm_crowdsec_init.lua` initialises the CrowdSec bouncer module once at
   nginx startup from `crowdsec-bouncer.conf` (rendered from the environment).
3. On each request to a protected host, `megoopm_crowdsec.lua`:
   - applies any active IP decision (ban/captcha/throttle) → the request is
     terminated at the edge; and
   - if the host has AppSec on, forwards the request to the AppSec component,
     which blocks malicious payloads before they reach the upstream.

Enforcement is **per host**: hosts with the toggle off never reference the Lua
handler, so they are untouched.

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

## Configuration

Environment (see `.env.example`):

- `CROWDSEC_LAPI_URL` — LAPI base URL (default `http://crowdsec:8080`).
- `CROWDSEC_BOUNCER_KEY` — bouncer API key; drives the nginx bouncer **and** the
  backend decision-read path. CrowdSec auto-registers it via `BOUNCER_KEY_megoopm`.
- `CROWDSEC_MACHINE_ID` / `CROWDSEC_MACHINE_PASSWORD` — enable the alert-read and
  manual-decision-write paths. Register once:
  ```
  docker compose exec crowdsec cscli machines add megoopm --password <pw>
  ```
  then set both env vars and restart the backend.

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
