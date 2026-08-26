# Engineering baseline (MEG-25)

Owner: QA. This documents the cross-cutting quality gates and the contract that
keeps the backend and frontend in sync. Update it when a gate changes.

## Continuous Integration

Two GitHub Actions workflows gate every pull request. A failure blocks merge.

### `backend-ci` (`.github/workflows/backend-ci.yml`)

Runs on changes under `backend/**`. Spins up Postgres, then:

1. `ruff check .` — lint
2. `ruff format --check .` — formatting
3. `alembic upgrade head` — migrations apply cleanly on a fresh DB
4. `alembic check` — **no ORM model changes are missing a migration**
5. `pytest` — unit + contract tests (includes the OpenAPI drift test below)

### `frontend-ci` (`.github/workflows/frontend-ci.yml`)

Runs on changes under `frontend/**` or to `backend/openapi.json`. With Node 20:

1. `npm ci`
2. **Generated API types are in sync** — regenerates `gen:api` and fails on drift
3. `npm run lint`
4. `npm run typecheck`
5. `npm run test`
6. `npm run build`

> Note: both workflows are path-filtered. If either job is made a *required*
> status check in branch protection, keep in mind GitHub leaves a required check
> that never triggers in a pending state — prefer requiring the job only for the
> paths it watches, or drop the path filter on the required job.

## OpenAPI publishing → frontend types (the FE/BE contract)

The backend is the single source of truth for the API shape. The pipeline:

```
FastAPI app  --(scripts/export_openapi.py)-->  backend/openapi.json
backend/openapi.json  --(npm run gen:api)-->  frontend/src/lib/api/generated/schema.ts
```

- FastAPI already serves the live document at `GET /openapi.json`.
- `backend/openapi.json` is the **committed, published** contract. Generate it
  with `python -m scripts.export_openapi` (from `backend/`).
- The frontend consumes it via `npm run gen:api` (openapi-typescript), which
  writes `src/lib/api/generated/schema.ts`. Never hand-edit that file. Feature
  code derives types from `src/lib/api/types.ts` instead of hand-authoring them.

### Contract for anyone who changes an API route

Because two generated artifacts are committed, changing/adding a route means:

1. `cd backend && python -m scripts.export_openapi` → commit `backend/openapi.json`
2. `cd frontend && npm run gen:api` → commit `src/lib/api/generated/schema.ts`

CI enforces both: `backend/tests/test_openapi.py` fails if `openapi.json` drifts
from the app, and `frontend-ci` fails if `schema.ts` drifts from `openapi.json`.
Regenerate and commit — do not edit the generated files by hand.

## Audit log

Status as of MEG-25 sign-off: the **model exists** but the write path and read
endpoint are **not yet implemented** — this is delegated backend feature work
(QA does not implement product features). See
[`docs/backlog/audit-log-endpoints.md`](./backlog/audit-log-endpoints.md) for
the delegated spec and acceptance criteria.
