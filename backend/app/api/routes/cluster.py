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

from fastapi import APIRouter

from app.api.deps import AdminUser, SessionDep
from app.schemas.cluster import ClusterStatus
from app.services.cluster.status import compute_cluster_status

router = APIRouter(tags=["cluster"])


@router.get("/status", response_model=ClusterStatus)
async def cluster_status(_admin: AdminUser, db: SessionDep) -> ClusterStatus:
    """Report the shared config version and how far each node has converged.

    The computation lives in the service so the dashboard reuses it rather than
    growing a second, divergent copy.
    """
    return await compute_cluster_status(db)
