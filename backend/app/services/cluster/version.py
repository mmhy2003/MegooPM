"""Shared config-version tracking + node-local reload marker (MEG-35).

Two pieces of state drive config propagation across HA nodes:

* The **shared** ``cluster_state.config_version`` in Postgres — the cluster's
  source of truth, bumped on every nginx apply that changes files. Read/written
  here through a plain SQLAlchemy :class:`~sqlalchemy.engine.Connection` so the
  same helpers work under the Celery task's sync engine and in tests.
* A **node-local** marker file recording the last version this node reloaded
  nginx for. It must live on node-local storage (never the shared volume): it
  answers "has *this* node caught up?". A brand-new node has no marker and reads
  as ``-1`` so it always reloads once to converge.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Connection, insert, select, update

from app.models.cluster_state import CLUSTER_STATE_ROW_ID, ClusterState

# Version a node reports before it has ever reconciled (marker absent). Lower
# than the seeded 0 so the first reconcile always fires.
UNKNOWN_LOCAL_VERSION = -1


def read_config_version(conn: Connection) -> int:
    """Return the cluster's current shared config version (0 if unseeded)."""
    value = conn.execute(
        select(ClusterState.config_version).where(ClusterState.id == CLUSTER_STATE_ROW_ID)
    ).scalar_one_or_none()
    return int(value) if value is not None else 0


def bump_config_version(conn: Connection, node_id: str | None = None) -> int:
    """Increment and return the shared config version, stamping ``node_id``.

    Callers must already hold the cross-node apply lock (see
    :func:`app.services.cluster.locks.apply_lock`) so the read-modify-write is
    serialised cluster-wide. The row is normally seeded by migration; the
    insert path covers a not-yet-seeded database (e.g. the SQLite test engine).
    """
    current = conn.execute(
        select(ClusterState.config_version).where(ClusterState.id == CLUSTER_STATE_ROW_ID)
    ).scalar_one_or_none()
    if current is None:
        conn.execute(
            insert(ClusterState.__table__).values(
                id=CLUSTER_STATE_ROW_ID, config_version=1, updated_by=node_id
            )
        )
        return 1
    new_version = int(current) + 1
    conn.execute(
        update(ClusterState.__table__)
        .where(ClusterState.id == CLUSTER_STATE_ROW_ID)
        .values(config_version=new_version, updated_by=node_id)
    )
    return new_version


def read_local_version(marker_path: str | os.PathLike[str]) -> int:
    """Return the config version this node last reloaded nginx for.

    ``UNKNOWN_LOCAL_VERSION`` when the marker is missing or unreadable, so a
    fresh node always reloads once to converge on the shared state.
    """
    try:
        return int(Path(marker_path).read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return UNKNOWN_LOCAL_VERSION


def write_local_version(marker_path: str | os.PathLike[str], version: int) -> None:
    """Atomically record that this node has reloaded nginx for ``version``."""
    path = Path(marker_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(str(version), encoding="utf-8")
    os.replace(tmp, path)


__all__ = [
    "UNKNOWN_LOCAL_VERSION",
    "bump_config_version",
    "read_config_version",
    "read_local_version",
    "write_local_version",
]
