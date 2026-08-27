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
├── docker-compose.yml   Full-stack local dev orchestration
└── Makefile             Developer shortcuts over docker compose
```

This is a monorepo: the frontend and backend live side by side and are developed
and deployed together.

## Quick start (full stack)

Run the entire system — Postgres, Redis, the API, Celery worker + beat, the web
UI, the managed nginx proxy, and a CrowdSec placeholder — with one command.
Requires Docker with the Compose plugin.

```bash
cp .env.example .env    # optional — sane defaults are baked into compose
docker compose up --build
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

The frontend reaches the backend at `NEXT_PUBLIC_API_BASE_URL`
(default `http://localhost:8000`). The backend writes managed vhosts and TLS
certs onto shared named volumes (`nginx_confd`, `nginx_certs`) that the nginx
container reads. `make help` lists the common tasks (`logs`, `migrate`, `psql`,
`clean`, …). See [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md#local-dev-orchestration)
for how the stack fits together.

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
