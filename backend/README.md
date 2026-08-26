# MegooPM Backend

FastAPI service (Python 3.12, SQLAlchemy 2.0 async, Postgres, Alembic).

## Layout

```
backend/
├── app/
│   ├── main.py            # ASGI app factory + entrypoint (app.main:app)
│   ├── api/
│   │   ├── router.py      # versioned API aggregation (mounted at /api/v1)
│   │   └── routes/        # route modules (health.py, ...)
│   ├── core/config.py     # pydantic-settings, env-driven configuration
│   ├── db/
│   │   ├── base.py        # DeclarativeBase + naming conventions
│   │   └── session.py     # async engine + get_session dependency
│   ├── models/            # ORM models (registered in __init__.py)
│   ├── schemas/           # Pydantic request/response models
│   └── services/          # business logic
├── alembic/               # migrations (async env.py)
├── tests/                 # pytest (asyncio_mode=auto)
├── Dockerfile
├── entrypoint.sh          # runs `alembic upgrade head` then the CMD
└── docker-compose.yml     # local Postgres + API
```

## Local development

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install .[dev]
cp .env.example .env        # adjust DATABASE_URL etc.

# with a local Postgres running:
alembic upgrade head
uvicorn app.main:app --reload
```

Liveness check: `curl http://localhost:8000/health` → `{"status":"ok",...}`.
Interactive docs: http://localhost:8000/docs

### With Docker

```bash
cd backend
docker compose up --build   # Postgres + API; migrations run on startup
```

## Configuration

All settings are env-driven (`app/core/config.py`, via pydantic-settings).
See `.env.example` for the full list. Key vars:

| Variable       | Purpose                                    |
| -------------- | ------------------------------------------ |
| `DATABASE_URL` | async SQLAlchemy URL (`postgresql+asyncpg://…`) |
| `SECRET_KEY`   | signing secret (set in every environment)  |
| `CORS_ORIGINS` | comma-separated allowed origins, or `*`    |
| `ENVIRONMENT`  | `development` / `staging` / `production`   |

## Migrations

```bash
alembic revision --autogenerate -m "add projects table"
alembic upgrade head
alembic downgrade -1
```

Import new models in `app/models/__init__.py` so autogenerate detects them.

## Quality gates

```bash
ruff check .          # lint
ruff format .         # format
pytest                # tests
```

CI runs all of the above plus `alembic upgrade head` against Postgres
(`.github/workflows/backend-ci.yml`).

## Conventions

See [`../docs/backend-conventions.md`](../docs/backend-conventions.md) for the
repo-wide conventions every backend ticket follows.
