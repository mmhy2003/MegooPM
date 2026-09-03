"""Read and record a maintenance job's last run.

Synchronous, like ``apply_state`` — Celery tasks are sync and drive
``app.services.cluster.sync_engine``. One row per kind: starting a run
replaces whatever the previous run left, so "running" never sits next to a
stale error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Connection, select

from app.models.crowdsec_job_run import CrowdSecJobRun
from app.models.enums import CrowdSecJobKind, CrowdSecJobTrigger


@dataclass(frozen=True, slots=True)
class JobRun:
    kind: CrowdSecJobKind
    started_at: datetime
    finished_at: datetime | None
    ok: bool
    error: str | None
    trigger: CrowdSecJobTrigger
    restarted: bool
    detail: dict


def read_job_run(conn: Connection, kind: CrowdSecJobKind) -> JobRun | None:
    table = CrowdSecJobRun.__table__
    row = conn.execute(select(table).where(table.c.kind == kind.value)).one_or_none()
    if row is None:
        return None
    return JobRun(
        kind=CrowdSecJobKind(row.kind),
        started_at=row.started_at,
        finished_at=row.finished_at,
        ok=row.ok,
        error=row.error,
        trigger=CrowdSecJobTrigger(row.trigger),
        restarted=row.restarted,
        detail=dict(row.detail or {}),
    )


def start_job_run(
    conn: Connection,
    kind: CrowdSecJobKind,
    *,
    trigger: CrowdSecJobTrigger,
    started_at: datetime | None = None,
) -> None:
    """Mark a run as in progress, wiping the previous outcome."""
    table = CrowdSecJobRun.__table__
    conn.execute(table.delete().where(table.c.kind == kind.value))
    conn.execute(
        table.insert().values(
            kind=kind.value,
            started_at=started_at or datetime.now(UTC),
            finished_at=None,
            ok=False,
            error=None,
            trigger=trigger.value,
            restarted=False,
            detail={},
        )
    )


def finish_job_run(
    conn: Connection,
    kind: CrowdSecJobKind,
    *,
    ok: bool,
    error: str | None,
    restarted: bool,
    detail: dict,
    finished_at: datetime | None = None,
) -> None:
    table = CrowdSecJobRun.__table__
    conn.execute(
        table.update()
        .where(table.c.kind == kind.value)
        .values(
            finished_at=finished_at or datetime.now(UTC),
            ok=ok,
            error=error,
            restarted=restarted,
            detail=detail,
        )
    )


__all__ = ["JobRun", "finish_job_run", "read_job_run", "start_job_run"]
