"""Cluster convergence, computed once and read from two places.

Extracted from ``GET /cluster/status`` so the dashboard's config-health card
reuses it rather than recomputing it. Two implementations of "is this cluster
converged" that could disagree would be worse than either being wrong alone —
an operator comparing the two pages would have no way to tell which lied.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.cluster_state import CLUSTER_STATE_ROW_ID, ClusterNode, ClusterState
from app.schemas.cluster import ClusterNodeStatus, ClusterStatus


async def compute_cluster_status(db: AsyncSession) -> ClusterStatus:
    """Report the shared config version and how far each node has converged."""
    version = (
        await db.scalar(
            select(ClusterState.config_version).where(ClusterState.id == CLUSTER_STATE_ROW_ID)
        )
        or 0
    )
    rows = (await db.scalars(select(ClusterNode).order_by(ClusterNode.node_id))).all()

    now = datetime.now(UTC)
    stale_after = settings.node_liveness_window_seconds
    nodes = [
        ClusterNodeStatus(
            node_id=row.node_id,
            applied_version=int(row.applied_version),
            last_seen_at=row.last_seen_at,
            in_sync=int(row.applied_version) >= int(version),
            stale=(now - row.last_seen_at).total_seconds() > stale_after
            if row.last_seen_at
            else True,
        )
        for row in rows
    ]
    return ClusterStatus(
        ha_enabled=settings.ha_enabled,
        config_version=int(version),
        this_node=settings.effective_node_id,
        nodes=nodes,
        converged=bool(nodes) and all(n.in_sync for n in nodes if not n.stale),
    )


__all__ = ["compute_cluster_status"]
