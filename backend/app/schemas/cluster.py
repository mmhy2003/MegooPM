"""Schemas for the cluster convergence endpoint."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ClusterNodeStatus(BaseModel):
    """One node's position relative to the shared config version."""

    node_id: str
    # The config version this node has actually reloaded its nginx for. ``-1``
    # means it has never reconciled (no marker file yet).
    applied_version: int
    last_seen_at: datetime | None = None
    # False means this node is still serving an older configuration.
    in_sync: bool
    # True means the node has stopped reporting: it is excluded from the push
    # fan-out, and ``in_sync`` reflects its last known state, not a live one.
    stale: bool


class ClusterStatus(BaseModel):
    """Cluster-wide convergence snapshot."""

    ha_enabled: bool
    config_version: int
    this_node: str
    nodes: list[ClusterNodeStatus]
    # True when every non-stale node has applied the current config version.
    converged: bool
