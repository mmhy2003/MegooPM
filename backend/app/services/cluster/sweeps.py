"""Period-idempotent claiming for cluster-wide periodic sweeps.

:func:`app.services.cluster.locks.leader_lock` answers "is anyone else running
this *right now*". That is the wrong question for a scheduled sweep. The lock is
held only for the body — for the certificate sweep, the few milliseconds it takes
to read the due list and enqueue renewals — so a second node's beat firing a
fraction of a second later finds it free and does the whole thing again. Since
beat runs on every node (each schedules its own nginx reconcile), every daily
sweep is emitted once per node, and every duplicate enqueues another ACME order
against Let's Encrypt's five-duplicates-per-week ceiling.

:func:`claim_sweep` answers the right question: "has this sweep already run for
this period?" On Postgres it is a single conditional upsert, so the check and the
claim cannot interleave — the guarantee holds without the leader lock, which
stays as cheap defence in depth.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.cluster_state import ClusterSweep


def claim_sweep(conn: Connection, name: str, *, min_interval_seconds: float) -> bool:
    """Claim ``name`` for this period; False if it already ran recently.

    The caller owns the transaction. ``min_interval_seconds`` should be
    comfortably below the sweep's schedule (half of it is a good default) so a
    genuine next period is never suppressed, and comfortably above the spread
    between nodes' beats so duplicates always are.
    """
    table = ClusterSweep.__table__

    if conn.dialect.name == "postgresql":
        # One statement: the row is only updated when the previous run has aged
        # out, and RETURNING tells us whether this caller is the one that won.
        stmt = pg_insert(table).values(name=name, last_run_at=func.now())
        stmt = stmt.on_conflict_do_update(
            index_elements=[table.c.name],
            set_={"last_run_at": func.now()},
            where=table.c.last_run_at
            < func.now() - func.make_interval(0, 0, 0, 0, 0, 0, min_interval_seconds),
        ).returning(table.c.name)
        return conn.execute(stmt).first() is not None

    # SQLite (tests, single-host): read-then-write is safe in one process.
    last = conn.execute(
        select(table.c.last_run_at).where(table.c.name == name)
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if last is not None:
        # SQLite hands back naive datetimes; they are stored as UTC.
        aware = last if last.tzinfo else last.replace(tzinfo=UTC)
        if now - aware < timedelta(seconds=min_interval_seconds):
            return False
        conn.execute(table.update().where(table.c.name == name).values(last_run_at=now))
    else:
        conn.execute(table.insert().values(name=name, last_run_at=now))
    return True


__all__ = ["claim_sweep"]
