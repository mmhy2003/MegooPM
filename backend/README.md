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
│   ├── services/          # business logic
│   ├── tasks/             # Celery task modules (sample.py, ...)
│   └── core/celery_app.py # Celery app + config + beat schedule
├── alembic/               # migrations (async env.py)
├── tests/                 # pytest (asyncio_mode=auto)
├── Dockerfile
├── entrypoint.sh          # API: runs `alembic upgrade head` then the CMD
├── worker-entrypoint.sh   # worker/beat: no migrations, just exec the CMD
└── docker-compose.yml     # local Postgres + Redis + API + worker + beat
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
docker compose up --build   # Postgres + Redis + API + Celery worker + beat
```

## Background tasks (Celery + Redis)

Async and scheduled jobs (certificate issuance/renewal and nginx config reloads,
added by later tickets) run on Celery with Redis as broker and result backend.
The Celery app (`app/core/celery_app.py`) reads its config from the same
`Settings`, so `REDIS_URL` is the single source of truth.

Run alongside a local Redis (`docker run -p 6379:6379 redis:7-alpine`):

```bash
# worker — consumes the queue and runs tasks
celery -A app.core.celery_app.celery_app worker --loglevel=info
# beat — schedules periodic jobs (heartbeat every 5 min by default)
celery -A app.core.celery_app.celery_app beat --loglevel=info
```

Task modules live in `app/tasks/` and are registered via `TASK_MODULES` in
`app/core/celery_app.py`; periodic jobs go in `celery_app.conf.beat_schedule`.
The worker/beat containers do **not** run migrations — the API owns schema.

Enqueue and poll the sample task through the API:

```bash
curl -sX POST localhost:8000/api/v1/tasks/sample -H 'content-type: application/json' -d '{"x":20,"y":22}'
# -> {"task_id":"...","status":"PENDING"}
curl -s localhost:8000/api/v1/tasks/<task_id>
# -> {"task_id":"...","status":"SUCCESS","ready":true,"result":42,"error":null}
```

Tests run Celery in **eager** mode (`CELERY_TASK_ALWAYS_EAGER=true`, configured
in `tests/conftest.py`), so the enqueue → execute → status-lookup path is covered
without a running broker or worker.

## Configuration

All settings are env-driven (`app/core/config.py`, via pydantic-settings).
See `.env.example` for the full list. Key vars:

| Variable       | Purpose                                    |
| -------------- | ------------------------------------------ |
| `DATABASE_URL` | async SQLAlchemy URL (`postgresql+asyncpg://…`) |
| `SECRET_KEY`   | signing secret (set in every environment)  |
| `CORS_ORIGINS` | comma-separated allowed origins, or `*`    |
| `ENVIRONMENT`  | `development` / `staging` / `production`   |
| `REDIS_URL`    | Celery broker + result backend (`redis://…`) |

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
