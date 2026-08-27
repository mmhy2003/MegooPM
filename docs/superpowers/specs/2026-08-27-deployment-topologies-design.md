# Deployment topologies: production, development, multi-node HA — design

Date: 2026-08-27 · Status: approved design, awaiting implementation plan

## Goal

Ship three self-contained compose files that each run alone:

| File | Purpose | Project name |
|---|---|---|
| `docker-compose.yml` | **Production, single node** — everything on one host, no Docker socket, production images, hardened env. | `megoopm-prod` |
| `docker-compose.dev.yml` | **Development** — hot reload for backend (API + worker) and frontend; keeps today's dev volumes and data. | `megoopm` |
| `docker-compose.ha.yml` | **Production, multi-node** — one file run *on every node*, node identity and shared-storage host path set in that node's `.env`. | `megoopm-ha` |

To make the production files honest, two missing pieces are built alongside:
a **socket-free reload agent** (worker → nginx validate/reload) and a
**production frontend image**.

## Non-goals

- A load balancer inside the stack (external LB; `infra/ha/haproxy.cfg` stays
  as an example).
- A built-in admin vhost on the managed nginx (UI/API stay on their own ports;
  TLS in front is the operator's).
- Managed/clustered Postgres or Redis (documented as an external concern).
- Changing the engine's validate → reload → rollback logic.

## Decisions taken during brainstorming

- Multi-node = **one compose file per node** (model A), not a single-host
  `--scale` simulation.
- No LB shipped (option 1).
- Admin UI and API exposed on their own ports; TLS is the operator's (option 1).
- Three **self-contained** compose files (no overrides/`extends`).
- Reload transport = an agent **inside the nginx container** (not a
  PID-namespace sidecar: `-s reload` reads `nginx.pid` from the sidecar's own
  filesystem, where it does not exist).
- One storage layout everywhere: `/data/nginx/conf.d`, `/data/nginx/conf.d/stream`,
  `/data/certs` (the settings already derive these from `SHARED_DATA_DIR=/data`).

## Reload agent (worker ↔ nginx, socket-free)

### Agent — `infra/nginx/reload-agent.sh`

- Served by `socat TCP-LISTEN:9099,fork,reuseaddr EXEC:/reload-agent.sh`,
  started by `infra/nginx/docker-entrypoint.sh` in a background restart loop
  (`while true; do socat …; sleep 1; done &`) before it `exec`s OpenResty.
- Port 9099 is never published: reachable only on the compose network.
- Reads one line: `<token> <command>`.
  - Token must equal `NGINX_RELOAD_TOKEN`. Empty configured token → every
    request is refused and the entrypoint logs a warning at start.
  - `command` ∈ `ping` (prints `pong`), `test`
    (`/usr/local/openresty/bin/openresty -p /usr/local/openresty/nginx -c /etc/nginx/nginx.conf -t`),
    `reload` (same binary, `-s reload`). Anything else → status 2.
  - No user-controlled arguments ever reach the shell.
- Streams the command's combined stdout+stderr, then a final line
  `__MEGOOPM_STATUS__ <exit code>`.

### Client — `backend/scripts/nginx_remote.py`

- `python -m scripts.nginx_remote <ping|test|reload>`.
- Env: `NGINX_AGENT_ADDR` (default `nginx:9099`), `NGINX_RELOAD_TOKEN`,
  `NGINX_AGENT_TIMEOUT_SECONDS` (default `30`).
- Connects, sends the line, reads to EOF, mirrors everything before the
  status line to **stderr** (the engine reads `nginx -t` output from stderr),
  and exits with the remote status. Missing status line → exit 70 with
  `agent returned no status`; connection refused / timeout → exit 111 with the
  address in the message; missing token → exit 64 before connecting.
- Production and dev compose files set
  `NGINX_TEST_COMMAND="python -m scripts.nginx_remote test"` and
  `NGINX_RELOAD_COMMAND="python -m scripts.nginx_remote reload"`.
  `app/services/nginx/controller.py` is unchanged.

### Health

The nginx healthcheck becomes
`wget -q --spider http://127.0.0.1/healthz && printf '%s ping\n' "$NGINX_RELOAD_TOKEN" | socat -T3 - TCP:127.0.0.1:9099 | grep -q pong`
so a dead agent marks the container unhealthy.

## Images

### `infra/nginx/Dockerfile`

`apk add socat`; `COPY reload-agent.sh /reload-agent.sh` (CR-stripped, executable,
like the entrypoint). `infra/nginx/nginx.conf` includes change to
`/data/nginx/conf.d/*.conf` and `/data/nginx/conf.d/stream/*.conf`; the header
comment is updated to describe `/data`.

### `frontend/Dockerfile` (multi-stage)

| Stage | Base | Role |
|---|---|---|
| `deps` | `node:22-alpine` | `npm ci` |
| `dev` | `deps` | `CMD npm run dev -- --hostname 0.0.0.0`; source arrives by bind mount |
| `builder` | `deps` | `ARG NEXT_PUBLIC_API_BASE_URL`, `ARG NEXT_PUBLIC_AUTH_ENABLED=false`, `npm run build` |
| `runner` | `node:22-alpine` | copies `.next/standalone`, `.next/static`, `public`; non-root `node`; `HOSTNAME=0.0.0.0 PORT=3000`; `CMD node server.js` |

`frontend/next.config.ts` gains `output: "standalone"`. Compose selects
`target: dev` (dev) or `target: runner` (both production files) and passes the
two `NEXT_PUBLIC_*` values as build args in production. Changing them requires
`docker compose build frontend` — documented.

### `backend/Dockerfile`

Unchanged. Dev hot reload is composed from outside the image (see below).

## Compose files

Common to all three: the `/data` layout; `NGINX_RELOAD_TOKEN` shared by the
worker and nginx; `NGINX_TEST_COMMAND`/`NGINX_RELOAD_COMMAND` pointing at the
client; `HA_LOCK_DIR=/var/run/megoopm` as an anonymous per-container volume;
an init step that creates `/data/nginx/conf.d/stream` and `/data/certs/_acme-challenge`
and chowns `/data` to `1000:1000`, failing loudly if it cannot.

### `docker-compose.yml` — production, single node

- Services: `db`, `redis`, `crowdsec`, `data-init`, `backend`, `worker`,
  `beat`, `frontend` (`target: runner`), `nginx`. All long-running services
  `restart: unless-stopped`. `db`/`redis`/`crowdsec` publish no ports.
- Required env (`${VAR:?message}`): `SECRET_KEY`, `POSTGRES_PASSWORD`,
  `CROWDSEC_BOUNCER_KEY`, `NGINX_RELOAD_TOKEN`, `NEXT_PUBLIC_API_BASE_URL`.
- Fixed: `ENVIRONMENT=production`, `DEBUG=false`, `HA_ENABLED=false`,
  `RUN_MIGRATIONS=1` on `backend` only.
- Defaults: `ACME_DIRECTORY_URL=https://acme-v02.api.letsencrypt.org/directory`,
  `NGINX_HTTP_PORT=80`, `NGINX_HTTPS_PORT=443`, `FRONTEND_PORT=3000`,
  `BACKEND_PORT=8000`, `CORS_ORIGINS` (must be set to the UI origin; default
  `http://localhost:3000`), `FIRST_ADMIN_EMAIL`/`FIRST_ADMIN_PASSWORD` empty
  (seed with `python -m scripts.create_user` instead), ACME propagation
  settings as in `.env.example`.
- Volumes: `pgdata`, `crowdsec_config`, `crowdsec_data`, `app_data` (mounted at
  `/data` in `backend`, `worker`, `nginx`).

### `docker-compose.dev.yml` — development

- `name: megoopm` and the existing volume names (`pgdata`, `nginx_confd`,
  `nginx_certs`, `crowdsec_*`), so the current dev database, certificates and
  hosts keep working: `nginx_confd` mounts at `/data/nginx/conf.d`,
  `nginx_certs` at `/data/certs` in `backend`, `worker`, `nginx`.
- Backend hot reload: `./backend:/app` bind mount; `backend` command
  `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`; `worker` command
  `watchfiles --filter python "celery -A app.core.celery_app.celery_app worker --loglevel=info" app`;
  `beat` unchanged (restart it manually after schedule changes);
  `WATCHFILES_FORCE_POLLING=true` so changes propagate through Docker Desktop
  bind mounts on Windows/macOS.
- Frontend hot reload: `build.target: dev`; `./frontend:/app` bind mount with an
  anonymous volume on `/app/node_modules` and `/app/.next`;
  `WATCHPACK_POLLING=true`, `CHOKIDAR_USEPOLLING=true`.
- No Docker socket, no `docker exec`; the worker uses the agent. `user: root`
  and `C_FORCE_ROOT` on the worker go away.
- `db`/`redis` still publish `POSTGRES_PORT`/`REDIS_PORT`; every variable has a
  safe dev default (`NGINX_RELOAD_TOKEN` defaults to `megoopm-dev-reload-token`);
  ACME stays on Let's Encrypt staging.

### `docker-compose.ha.yml` — production, per node

- Always: `data-init`, `backend`, `worker`, `nginx`, `frontend` (`target: runner`),
  all `restart: unless-stopped`.
- Required per-node env: `NODE_ID`, `SHARED_DATA_PATH` (host directory of the
  shared mount, bind-mounted to `/data`), `SECRET_KEY`, `NGINX_RELOAD_TOKEN`,
  `CROWDSEC_BOUNCER_KEY`, `NEXT_PUBLIC_API_BASE_URL`, and — unless the
  `control-plane` profile is on — `DATABASE_URL`, `REDIS_URL`,
  `CROWDSEC_LAPI_URL`, `CROWDSEC_APPSEC_URL`.
- `HA_ENABLED=true`, `NODE_ID` passed through, `SHARED_DATA_DIR=/data`,
  `HA_RECONCILE_INTERVAL_SECONDS` default `15`, `RUN_MIGRATIONS=${RUN_MIGRATIONS:-0}`
  (set `1` on the node you upgrade first).
- Profiles (chosen per node with `COMPOSE_PROFILES` in that node's `.env`):
  - `control-plane`: `db`, `redis`, `crowdsec`, publishing `5432`, `6379`,
    `8080` (LAPI) and `7422` (AppSec) on `${CONTROL_PLANE_BIND:-0.0.0.0}`; the
    in-file defaults for `DATABASE_URL`/`REDIS_URL`/`CROWDSEC_*_URL` then
    resolve to the local services. Local volumes `pgdata`, `crowdsec_*`.
  - `scheduler`: the single `beat`.
- Ports on every node: nginx `80`/`443`, UI `3000`, API `8000` (all
  overridable). The external LB fronts `80`/`443` and, if desired, `3000`/`8000`.
- `infra/ha/haproxy.cfg` is rewritten as an **external** example: static
  `server nodeA 10.0.0.11:80 check` style entries, no Docker resolver.

## Env templates

- `.env.example` — single file for `docker-compose.yml` **and**
  `docker-compose.dev.yml`, in sections: *required for production* (blank,
  commented with how to generate: `openssl rand -hex 32`), *ports*, *ACME*,
  *CrowdSec*, *dev-only notes*. Dev needs no `.env` at all.
- `.env.ha.example` — per-node template: `NODE_ID`, `SHARED_DATA_PATH`,
  `COMPOSE_PROFILES`, shared-service URLs, the secrets, `RUN_MIGRATIONS`.

## Tooling and docs

- `Makefile`: `COMPOSE := docker compose -f docker-compose.dev.yml` for the
  existing dev targets; new `prod-up`, `prod-down`, `prod-logs`, `ha-up`,
  `ha-down`, `ha-logs` wrapping the other files.
- `backend/docker-compose.yml` (old API-only stack) is deleted; its README
  mention is removed.
- `README.md`: quick start uses the dev file; new "Deploying" section
  (single node, multi-node, env templates, `NEXT_PUBLIC_*` rebuild note).
- `docs/ha.md`: §2 table adds `SHARED_DATA_PATH` (host) → `/data`; §6 rewritten
  for per-node compose (profiles, first-node migration, NFS uid 1000 /
  `root_squash` guidance); §7 updated; §9 adds `SHARED_DATA_PATH`,
  `NGINX_RELOAD_TOKEN`, `NGINX_AGENT_ADDR`, `COMPOSE_PROFILES`.
- `docs/nginx-engine.md`: MEG-28 section describes the in-container agent,
  records why the PID-namespace sidecar was dropped, and lists all three
  files with "socket: no".
- `docs/CONVENTIONS.md`: local-dev orchestration paragraph renamed to the dev
  file.

## Error handling

| Situation | Behaviour |
|---|---|
| Required env missing | `docker compose` fails at parse time naming the variable (`:?`). |
| Agent unreachable / wrong token / no status line | `nginx_remote` exits non-zero with a message; the engine treats it as a failed validation and keeps the last-known-good config. |
| Agent process dies | healthcheck `ping` fails → container unhealthy; the entrypoint loop restarts socat within a second. |
| `/data` not writable by uid 1000 (NFS `root_squash`) | `data-init` fails with a message naming the path; dependent services never start. |
| Second `beat` started on another node | Sweeps are leader-locked (existing behaviour); documented as safe but unnecessary. |

## Testing

- `backend/tests/test_nginx_remote.py`: in-process fake agent (threaded TCP
  server) covering `ping`, status propagation (0 and non-zero), output mirrored
  to stderr, missing status line → 70, wrong/missing token, refused connection
  → 111, timeout.
- `backend/tests/test_compose_config.py`: for each compose file, run
  `docker compose -f <file> --env-file <example env> config -q`; skipped when
  the `docker` CLI is unavailable (CI runs it if Docker is present).
- Live verification on this machine (plan tasks):
  1. Dev file up with existing data; edit a backend file → uvicorn restarts;
     create a host → `openresty -t` via the agent succeeds; frontend hot reload
     observed.
  2. `docker-compose.yml` with a throwaway `.env` on alternate ports: stack
     healthy, UI served by the `runner` image, a proxy host created through the
     API renders and validates through the agent.
  3. `docker-compose.ha.yml` once with `COMPOSE_PROFILES=control-plane,scheduler`,
     `NODE_ID=node-a`, a local `SHARED_DATA_PATH`: healthy, `cluster_state`
     stamped with `node-a` after a config write.

## Files touched

New: `infra/nginx/reload-agent.sh`, `backend/scripts/nginx_remote.py`,
`backend/tests/test_nginx_remote.py`, `backend/tests/test_compose_config.py`,
`docker-compose.dev.yml`, `.env.ha.example`.

Modified: `docker-compose.yml` (rewritten as production), `docker-compose.ha.yml`
(rewritten per-node), `infra/nginx/Dockerfile`, `infra/nginx/docker-entrypoint.sh`,
`infra/nginx/nginx.conf`, `infra/ha/haproxy.cfg`, `frontend/Dockerfile`,
`frontend/next.config.ts`, `.env.example`, `Makefile`, `README.md`,
`docs/ha.md`, `docs/nginx-engine.md`, `docs/CONVENTIONS.md`.

Deleted: `backend/docker-compose.yml`.
