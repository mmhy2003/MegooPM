"""Cluster convergence endpoint (admin-only).

``GET /cluster/status`` answers the question an HA operator actually has: *is
every node serving the current configuration?* It compares the shared
``cluster_state.config_version`` against each node's ``applied_version`` from
the node registry, so a lagging node is visible from one call instead of
shelling into every host to read its reload-marker file.

Read-only and advisory: nothing here drives propagation. A node absent from the
registry has simply not reconciled since the table was created; it converges on
its own next tick.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import AdminUser, SessionDep
from app.core.config import settings
from app.models.cluster_state import CLUSTER_STATE_ROW_ID, ClusterNode, ClusterState
from app.schemas.cluster import ClusterNodeStatus, ClusterStatus

router = APIRouter(tags=["cluster"])


@router.get("/status", response_model=ClusterStatus)
async def cluster_status(_admin: AdminUser, db: SessionDep) -> ClusterStatus:
    """Report the shared config version and how far each node has converged."""
    version = (
        await db.scalar(
            select(ClusterState.config_version).where(
                ClusterState.id == CLUSTER_STATE_ROW_ID
            )
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
