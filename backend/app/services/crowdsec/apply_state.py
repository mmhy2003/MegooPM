"""Read and record whether the last whitelist render actually reached CrowdSec.

A single row (``id=1``), seeded by migration 0016. The apply runs in a Celery
task on the control-plane node and can fail long after the API returned 200; the
UI reads this so it never shows a whitelist as active when CrowdSec has never
seen it.

Synchronous, like the cluster helpers — Celery tasks are sync and drive
``app.services.cluster.sync_engine``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, func, select, update

from app.models.crowdsec_whitelist import CrowdSecWhitelistApply

_ROW_ID = 1


@dataclass(frozen=True, slots=True)
class ApplyState:
    """The last apply attempt, as recorded."""

    applied_digest: str | None
    applied_at: datetime | None
    ok: bool
    error: str | None


def read_apply_state(conn: Connection) -> ApplyState:
    """The recorded state, or a neutral default when the row is missing."""
    table = CrowdSecWhitelistApply.__table__
    row = conn.execute(select(table).where(table.c.id == _ROW_ID)).one_or_none()
    if row is None:
        return ApplyState(applied_digest=None, applied_at=None, ok=True, error=None)
    return ApplyState(
        applied_digest=row.applied_digest,
        applied_at=row.applied_at,
        ok=row.ok,
        error=row.error,
    )


def record_apply(
    conn: Connection, *, digest: str | None, ok: bool, error: str | None
) -> None:
    """Record the outcome.

    ``applied_digest`` only advances on success, so a failed apply leaves the
    digest pointing at the content actually on disk — which is what makes the
    next attempt notice there is still work to do rather than short-circuiting.
    """
    table = CrowdSecWhitelistApply.__table__
    values: dict[str, object] = {"ok": ok, "error": error, "applied_at": func.now()}
    if ok and digest is not None:
        values["applied_digest"] = digest
    result = conn.execute(update(table).where(table.c.id == _ROW_ID).values(**values))
    if result.rowcount == 0:  # pragma: no cover - migration seeds the row
        conn.execute(
            table.insert().values(id=_ROW_ID, ok=ok, error=error, applied_digest=digest)
        )


__all__ = ["ApplyState", "read_apply_state", "record_apply"]
