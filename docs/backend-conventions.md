# MegooPM Backend Conventions

These are the conventions every backend ticket follows. They exist so any agent
can pick up a ticket cold and produce code that matches the rest of the repo.
Owned by the Architect; propose changes via an issue rather than editing ad hoc.

## Stack

- **Python 3.12**, FastAPI, SQLAlchemy 2.0 (async), Postgres, Alembic.
- Config via **pydantic-settings** (`app/core/config.py`) — always env-driven.
- Lint/format via **ruff**; tests via **pytest** (`asyncio_mode = auto`).

## Layout & layering

```
app/api/routes/   HTTP surface only — parse/validate, call a service, return a schema
app/services/     business logic; no FastAPI imports, no raw request/response types
app/models/       SQLAlchemy ORM models (one module per aggregate)
app/schemas/      Pydantic request/response models (never expose ORM models directly)
app/db/           engine, session, DeclarativeBase
app/core/         config and cross-cutting concerns
```

Dependency direction: `api → services → models/db`. Routes never contain
business logic; services never import from `app.api`.

## Async everywhere

- All DB access uses the async engine and `AsyncSession`.
- Inject sessions with `Depends(get_session)` (`app/db/session.py`). Never
  create sessions inline in a route.
- Route handlers and service functions that touch IO are `async def`.

## Database & migrations

- Every schema change ships an Alembic migration in the same PR. Never mutate
  the DB outside a migration.
- Register new models in `app/models/__init__.py` so autogenerate sees them.
- Migrations must have working `upgrade()` **and** `downgrade()`.
- Constraint/index names come from the naming convention in `app/db/base.py` —
  don't hand-name them.
- Table names: plural snake_case (`projects`, `project_members`). Columns:
  snake_case. Primary keys: `id`. Timestamps: `created_at`, `updated_at`.

## API conventions

- Application routes are versioned under `settings.api_v1_prefix` (`/api/v1`).
  Attach feature routers to `app.api.router.api_router`.
- `/health` is the only unversioned route (liveness; no DB access).
- Response bodies are Pydantic schemas with `response_model` set.
- Use plural nouns for collections (`/projects`, `/projects/{id}`).

## Configuration

- Add new settings to `Settings` with a sensible default and document them in
  `.env.example`. Read them via `from app.core.config import settings`.
- Secrets never have real defaults committed — placeholders only.

## Testing

- Every ticket adds/updates tests. Unit-test services directly; test routes via
  httpx `ASGITransport` (see `tests/conftest.py`).
- CI gates: `ruff check`, `ruff format --check`, `alembic upgrade head`,
  `pytest`. Keep them green.

## Definition of done (backend ticket)

1. Code follows the layering above.
2. Schema changes include a migration that upgrades and downgrades cleanly.
3. `ruff check .`, `ruff format --check .`, and `pytest` pass locally.
4. New settings documented in `.env.example`.
5. Endpoints have `response_model` schemas and at least one test.
