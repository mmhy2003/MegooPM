"""Cluster coordination for HA multi-node deployments (MEG-35).

Facade over the two coordination concerns:

* **Locking** (:mod:`.locks`) — a cross-node exclusive lock around nginx apply
  and a non-blocking leader lock for singleton periodic jobs.
* **Versioning** (:mod:`.version`) — the shared ``config_version`` counter and
  the node-local reload marker that together propagate config changes.
* **Membership** (:mod:`.nodes`) — the per-node registry that names the fan-out
  targets and exposes how far each node has converged.
* **Sweep claims** (:mod:`.sweeps`) — makes a scheduled sweep run once per
  period, not once per beat, which a mutual-exclusion lock cannot do.

:func:`sync_engine` builds the short-lived synchronous engine the Celery tasks
use for these operations (they run outside FastAPI's event loop).
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.services.cluster.locks import APPLY_LOCK_KEY, apply_lock, leader_lock
from app.services.cluster.nodes import (
    NodeState,
    live_peers,
    node_states,
    record_node_state,
)
from app.services.cluster.sweeps import claim_sweep
from app.services.cluster.version import (
    UNKNOWN_LOCAL_VERSION,
    bump_config_version,
    read_config_version,
    read_local_version,
    write_local_version,
)


def sync_engine() -> Engine:
    """A short-lived synchronous engine for cluster coordination in tasks.

    Uses ``NullPool`` — coordination calls are brief and infrequent, so a
    per-call connection is simpler than holding a pool open in every worker.
    """
    return create_engine(settings.sync_database_url, poolclass=NullPool, future=True)


__all__ = [
    "APPLY_LOCK_KEY",
    "UNKNOWN_LOCAL_VERSION",
    "NodeState",
    "apply_lock",
    "bump_config_version",
    "claim_sweep",
    "leader_lock",
    "live_peers",
    "node_states",
    "read_config_version",
    "read_local_version",
    "record_node_state",
    "sync_engine",
    "write_local_version",
]
