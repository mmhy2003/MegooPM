# Backlog: Audit log write path + read endpoint

Delegated by QA (MEG-25). This is backend feature work — QA owns the CI/quality
gates for it, not the implementation. The remaining pieces satisfy two MEG-25
acceptance criteria that the current code does not yet meet.

## What already exists

- `app/models/audit_log.py` — `AuditLog` model (`actor`, `action`,
  `object_type`, `object_id`, `meta`, `created_at`), registered in
  `app/models/__init__.py`.
- `app/models/enums.py` — `AuditAction` (`create`, `update`, `delete`, `enable`,
  `disable`).

## What is missing (the deliverable)

1. **A migration** creating the `audit_log` table (with the two indexes declared
   on the model) — verify with `alembic check` (CI gate).
2. **A write path** for privileged mutations. Add a reusable service helper, e.g.
   `app/services/audit.py::record_audit(session, *, actor, action, object_type,
   object_id=None, meta=None)`, and call it from the create/update/delete/enable/
   disable handlers of privileged resources (proxy hosts, upstreams,
   certificates, access lists, streams, users …). `actor` comes from the
   authenticated principal (nullable for system actions).
3. **A read endpoint**, e.g. `GET /api/v1/audit-log`, returning entries newest
   first with pagination and filters (`object_type`, `object_id`, `actor`,
   `action`). Access should be restricted to privileged/admin users.
4. **A response schema** (`app/schemas/audit.py`) and router registration.
5. Regenerate the API contract after adding the endpoint:
   `python -m scripts.export_openapi` and `npm run gen:api`, then commit both
   (see `docs/engineering-baseline.md`).

## Acceptance criteria (from MEG-25)

- Privileged mutations write audit-log entries with actor, action and timestamp.
- Audit log is queryable via an endpoint.

## QA verification plan (to be added once endpoints land)

- Integration test: a privileged create/update/delete produces exactly one
  `audit_log` row with the correct `actor`, `action`, `object_type`,
  `object_id`, and a populated `created_at`.
- Integration test: `GET /api/v1/audit-log` returns entries newest-first, honors
  filters, and rejects unauthenticated / non-privileged callers.
- Contract: the new endpoint appears in `backend/openapi.json` and the generated
  frontend types (drift gates enforce this automatically).
