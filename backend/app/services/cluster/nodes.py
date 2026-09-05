"""Node registry: who is in the cluster, and how far each node has converged.

Every :func:`app.tasks.nginx.reconcile_local_nginx` run records the calling
node's ``applied_version`` and refreshes its heartbeat here. That gives two
things the HA path needs:

* :func:`live_peers` — the fan-out target list. The applying node pushes a
  reconcile onto each live peer's *own* queue (``megoopm.node.<NODE_ID>``).
  Nodes that stopped reporting fall out of the list, so a decommissioned node
  never accumulates an unbounded queue of reconciles nobody will ever consume.
* :func:`node_states` — the convergence view. Comparing each node's
  ``applied_version`` to ``cluster_state.config_version`` answers "is the
  cluster in sync?" in one query instead of reading a marker file on every host.

The registry is advisory, never authoritative: propagation correctness rests on
the shared ``config_version`` plus each node's periodic self-reconcile. A node
missing from this table still converges on its next tick — it just misses the
low-latency push.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.cluster_state import ClusterNode


@dataclass(frozen=True, slots=True)
class NodeState:
    """One node's convergence state, as recorded at its last reconcile."""

    node_id: str
    applied_version: int
    last_seen_at: datetime | None


def record_node_state(conn: Connection, node_id: str, applied_version: int) -> None:
    """Upsert this node's heartbeat and last-applied config version.

    Uses an ``ON CONFLICT`` upsert on Postgres. Other dialects (the SQLite test
    engine) get an update-then-insert fallback, which is race-free enough for a
    single-process test and never runs in a real cluster.
    """
    table = ClusterNode.__table__
    if conn.dialect.name == "postgresql":
        stmt = pg_insert(table).values(
            node_id=node_id, applied_version=applied_version, last_seen_at=func.now()
        )
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=[table.c.node_id],
                set_={
                    "applied_version": stmt.excluded.applied_version,
                    "last_seen_at": func.now(),
                },
            )
        )
        return

    updated = conn.execute(
        table.update()
        .where(table.c.node_id == node_id)
        .values(applied_version=applied_version, last_seen_at=func.now())
    ).rowcount
    if not updated:
        conn.execute(
            table.insert().values(
                node_id=node_id, applied_version=applied_version, last_seen_at=func.now()
            )
        )


def live_peers(conn: Connection, *, exclude: str, max_age_seconds: float) -> list[str]:
    """Node ids seen within ``max_age_seconds``, excluding ``exclude``.

    ``max_age_seconds`` should be a small multiple of the reconcile interval, so
    a node that is merely slow stays in the fan-out while a node that is gone
    drops out within a few ticks.
    """
    table = ClusterNode.__table__
    cutoff = func.now() - func.make_interval(0, 0, 0, 0, 0, 0, max_age_seconds)
    if conn.dialect.name != "postgresql":
        # SQLite (tests): no make_interval; every registered node counts.
        rows = conn.execute(select(table.c.node_id).where(table.c.node_id != exclude)).scalars()
        return sorted(rows)
    rows = conn.execute(
        select(table.c.node_id).where(table.c.node_id != exclude, table.c.last_seen_at >= cutoff)
    ).scalars()
    return sorted(rows)


def node_states(conn: Connection) -> list[NodeState]:
    """Every registered node's convergence state, ordered by node id."""
    table = ClusterNode.__table__
    rows = conn.execute(
        select(table.c.node_id, table.c.applied_version, table.c.last_seen_at).order_by(
            table.c.node_id
        )
    ).all()
    return [
        NodeState(
            node_id=r.node_id,
            applied_version=int(r.applied_version),
            last_seen_at=r.last_seen_at,
        )
        for r in rows
    ]


__all__ = ["NodeState", "live_peers", "node_states", "record_node_state"]
