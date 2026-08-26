#!/usr/bin/env bash
# Container entrypoint for Celery workers and the beat scheduler.
#
# Unlike the API entrypoint this does NOT run database migrations: the API /
# migration job owns schema management, and a fleet of workers must not race to
# apply migrations. Workers only need the code and a reachable broker.
set -euo pipefail

echo "[worker-entrypoint] starting: $*"
exec "$@"
