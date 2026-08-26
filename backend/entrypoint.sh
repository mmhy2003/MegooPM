#!/usr/bin/env bash
# Container entrypoint: apply database migrations, then exec the given command
# (uvicorn by default). Migrations can be skipped by setting RUN_MIGRATIONS=0,
# which is useful when a separate job owns schema management.
set -euo pipefail

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "[entrypoint] applying database migrations (alembic upgrade head)..."
  alembic upgrade head
else
  echo "[entrypoint] RUN_MIGRATIONS=0, skipping migrations."
fi

echo "[entrypoint] starting: $*"
exec "$@"
