"""Cluster-wide coordination state for HA deployments (MEG-35).

A single row (``id = 1``) holding the monotonically increasing
``config_version``. Every successful nginx *apply* bumps this counter inside the
same advisory-locked transaction; each node compares it to a node-local marker
to decide whether its local nginx needs a reload. This is the source of truth
for config propagation across nodes — the shared ``conf.d`` volume holds the
actual file bytes, and ``apply_config`` is idempotent, so a node that sees a
newer version simply reloads and converges.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# The single row that always exists once the cluster has applied config once.
CLUSTER_STATE_ROW_ID = 1


class ClusterState(Base):
    """Singleton row tracking the cluster's current nginx config version."""

    __tablename__ = "cluster_state"

    # Fixed to ``CLUSTER_STATE_ROW_ID`` — this table only ever holds one row.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    # Monotonic counter; bumped on every config change that reloads nginx.
    config_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    # Node that performed the most recent bump (observability only).
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ClusterNode(Base):
    """One row per node, recording how far that node has converged.

    Written by every ``reconcile_local_nginx`` run. Serves two purposes:

    * **Fan-out targets** — the applying node reads the recently-seen rows to
      decide which per-node queues to push a reconcile onto. A node that has
      stopped reporting drops out of the fan-out instead of accumulating an
      unbounded queue.
    * **Observability** — comparing ``applied_version`` here to
      :attr:`ClusterState.config_version` answers "has the cluster converged?"
      from one query, rather than shelling into every node to read its marker.
    """

    __tablename__ = "cluster_node"

    # ``Settings.effective_node_id`` — NODE_ID, or the hostname when unset.
    node_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    # The config version this node has actually reloaded nginx for.
    applied_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    # Heartbeat: refreshed on every reconcile, so staleness is detectable.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ClusterSweep(Base):
    """Last run of each cluster-wide periodic sweep, keyed by sweep name.

    ``leader_lock`` makes a sweep mutually exclusive; it does not make it happen
    once per period. The lock is held only for the body — for the renewal sweep,
    long enough to read the due list and enqueue — so a second node's beat firing
    a fraction of a second later finds the lock free and repeats the work. With
    beat running on every node that is the normal case, not an edge case.

    Claiming against this table closes that gap: the claim is conditional on the
    previous run being older than the sweep's interval, so only the first beat of
    each period does the work however many nodes fire it.
    """

    __tablename__ = "cluster_sweep"

    # Matches the name passed to ``leader_lock`` (e.g. "cert-renew-sweep").
    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["ClusterState", "ClusterNode", "ClusterSweep", "CLUSTER_STATE_ROW_ID"]
