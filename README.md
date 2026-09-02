# MegooPM

Self-hosted reverse-proxy management — proxy hosts, TLS certificates, access
lists, TCP/UDP streams, and CrowdSec-based security, in one UI.

## Repository layout

```
megoopm/
├── frontend/            Next.js 16 (App Router) + shadcn/ui — the web UI
├── backend/             FastAPI + Alembic + Postgres + Celery — the API
├── infra/               nginx base config and other infra assets
├── docs/                Architecture and repo conventions
├── docker-compose.yml       Production, single node
├── docker-compose.dev.yml   Development with hot reload
├── docker-compose.ha.yml    Production, one file per node of a cluster
└── Makefile                 Shortcuts over docker compose (dev, prod-*, ha-*)
```

This is a monorepo: the frontend and backend live side by side and are developed
and deployed together.

## Quick start (full stack)

Run the entire system — Postgres, Redis, the API, Celery worker + beat, the web
UI, the managed nginx proxy, and a CrowdSec placeholder — with one command.
Requires Docker with the Compose plugin.

```bash
cp .env.example .env    # optional — the dev file has sane defaults baked in
docker compose -f docker-compose.dev.yml up --build
# or: make up            (detached)   /   make up-fg (foreground)
```

Once healthy:

| Service        | URL                                            |
| -------------- | ---------------------------------------------- |
| Web UI         | http://localhost:3000                          |
| Backend API    | http://localhost:8000 (`/health`, `/docs`)     |
| Managed proxy  | http://localhost:8080                          |

**Default login:** `admin@example.com` / `changeme`. On the very first start
(users table empty) the backend seeds this admin from `FIRST_ADMIN_EMAIL` /
`FIRST_ADMIN_PASSWORD`; set those in `.env` before the first `docker compose up`
to pick your own, and change the password after signing in. The account is only
ever created on an empty database, so deleting it later is safe. Sign in at
http://localhost:3000/login (route guarding is off by default — set
`NEXT_PUBLIC_AUTH_ENABLED=true` to require it).
Admins manage accounts and roles at http://localhost:3000/users; everyone can
change their own name and password at http://localhost:3000/profile (click the avatar).
Wildcard / DNS-01 certificates: save your DNS provider's API credentials under
Certificates → DNS providers, then pick them in the new-certificate dialog —
see [`docs/certificates-dns01.md`](docs/certificates-dns01.md).

The frontend reaches the backend at `NEXT_PUBLIC_API_BASE_URL` (default
`http://localhost:8000`). The backend writes managed vhosts and TLS certs under
`/data` (shared with the nginx container), and the worker validates/reloads
nginx through a token-gated agent inside the nginx container — no Docker
socket anywhere. Edits under `backend/` and `frontend/` hot-reload. `make help`
lists the common tasks. See [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md#local-dev-orchestration)
for how the stack fits together.

## Deploying

| Topology | File | Env template | Start |
| --- | --- | --- | --- |
| Single node | `docker-compose.yml` | `.env.example` → `.env` | `docker compose up -d --build` (`make prod-up`) |
| Multi-node (run on every node) | `docker-compose.ha.yml` | `.env.ha.example` → `.env` | `docker compose -f docker-compose.ha.yml up -d --build` (`make ha-up`) |

Production refuses to start until the required secrets in the template are
set. `NEXT_PUBLIC_*` values are baked into the UI image at build time — rebuild
the frontend after changing them. For a cluster, follow the step-by-step
[`HA-SETUP.md`](HA-SETUP.md) (shared storage, control-plane vs data-plane
nodes, profiles, load balancer, verification); the design rationale is in
[`docs/ha.md`](docs/ha.md).

Working on just one side? Each package still runs standalone below.

## Frontend

```bash
cd frontend
cp .env.example .env.local     # point NEXT_PUBLIC_API_BASE_URL at your backend
npm install
npm run dev                    # http://localhost:3000
```

Quality gates (all run in CI):

```bash
npm run lint
npm run typecheck
npm run test
```

See [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md) for the stack, directory
conventions, and how features are wired into the app shell and API client.

## Attribution

Visitor analytics resolves countries using the
[DB-IP IP-to-Country Lite](https://db-ip.com/db/download/ip-to-country-lite)
database, licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
The licence requires this credit; keep it if you redistribute the image.

The database is downloaded at image build time on a best-effort basis. If it is
absent, visitor rows are still recorded — they simply carry no country.

