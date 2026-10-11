"""Restore-in-progress flag, shared by the worker and the API.

Redis, not the database: the database is the thing being replaced. The flag
carries a TTL so a worker killed mid-restore cannot wedge the API permanently —
it unblocks itself, and the next request finds a database that either restored
or did not.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import redis

RESTORE_KEY = "megoopm:restore"
#: Long enough for a large restore, short enough to self-heal after a crash.
RESTORE_TTL_S = 3600
_LAST_ERROR_KEY = "megoopm:restore:last-error"
_LAST_ERROR_TTL_S = 900
#: When a restore last finished. The API watches this to drop its connection
#: pool: asyncpg caches a prepared statement per query, and a restore replaces
#: the schema those plans were built against.
_RESTORED_AT_KEY = "megoopm:restore:completed-at"
_RESTORED_AT_TTL_S = 86400


def begin_restore(client: redis.Redis, step: str) -> None:
    client.delete(_LAST_ERROR_KEY)
    client.set(
        RESTORE_KEY,
        json.dumps({"started_at": datetime.now(UTC).isoformat(), "step": step}),
        ex=RESTORE_TTL_S,
    )


def set_step(client: redis.Redis, step: str) -> None:
    raw = client.get(RESTORE_KEY)
    payload = json.loads(raw) if raw else {"started_at": datetime.now(UTC).isoformat()}
    payload["step"] = step
    client.set(RESTORE_KEY, json.dumps(payload), ex=RESTORE_TTL_S)


def end_restore(client: redis.Redis, *, error: str | None = None) -> None:
    client.delete(RESTORE_KEY)
    if error:
        client.set(_LAST_ERROR_KEY, error, ex=_LAST_ERROR_TTL_S)


def is_restoring(client: redis.Redis) -> bool:
    return client.exists(RESTORE_KEY) == 1


def restore_status(client: redis.Redis) -> dict[str, Any]:
    raw = client.get(RESTORE_KEY)
    error = client.get(_LAST_ERROR_KEY)
    payload = json.loads(raw) if raw else {}
    return {
        "running": raw is not None,
        "step": payload.get("step"),
        "started_at": payload.get("started_at"),
        "error": error.decode() if isinstance(error, bytes) else error,
    }


def mark_restored(client: redis.Redis) -> None:
    """Record that a restore just replaced the database."""
    client.set(_RESTORED_AT_KEY, datetime.now(UTC).isoformat(), ex=_RESTORED_AT_TTL_S)


def restored_at(client: redis.Redis) -> str | None:
    """When the last restore finished, as the API saw it, or None."""
    raw = client.get(_RESTORED_AT_KEY)
    return raw.decode() if isinstance(raw, bytes) else raw


# --- async twins -------------------------------------------------------------
# The worker is synchronous and the API is not. Two small readers rather than
# one, because a blocking socket read inside an async request handler stalls
# the event loop for every other request in flight.


async def is_restoring_async(client: Any) -> bool:
    return await client.exists(RESTORE_KEY) == 1


async def restored_at_async(client: Any) -> str | None:
    raw = await client.get(_RESTORED_AT_KEY)
    return raw.decode() if isinstance(raw, bytes) else raw


async def restore_status_async(client: Any) -> dict[str, Any]:
    raw = await client.get(RESTORE_KEY)
    error = await client.get(_LAST_ERROR_KEY)
    if isinstance(raw, bytes):
        raw = raw.decode()
    payload = json.loads(raw) if raw else {}
    return {
        "running": raw is not None,
        "step": payload.get("step"),
        "started_at": payload.get("started_at"),
        "error": error.decode() if isinstance(error, bytes) else error,
    }
