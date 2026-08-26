# MegooPM

Monorepo for MegooPM.

## Structure

```
backend/    FastAPI service (Python 3.12, SQLAlchemy async, Postgres, Alembic)
frontend/   Next.js app (added by frontend foundation ticket)
docs/       architecture & conventions
```

## Getting started

- Backend: see [`backend/README.md`](backend/README.md).
- Conventions: [`docs/backend-conventions.md`](docs/backend-conventions.md).

The backend is the root dependency for backend work (MEG-10). Start Postgres,
run `alembic upgrade head`, then `uvicorn app.main:app --reload`.
