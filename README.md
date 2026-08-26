# MegooPM

Self-hosted reverse-proxy management — proxy hosts, TLS certificates, access
lists, TCP/UDP streams, and CrowdSec-based security, in one UI.

## Repository layout

```
megoopm/
├── frontend/        Next.js 16 (App Router) + shadcn/ui — the web UI
├── backend/         FastAPI + Alembic + Postgres — the API (added by later tickets)
└── docs/            Architecture and repo conventions
```

This is a monorepo: the frontend and backend live side by side and are developed
and deployed together.

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
