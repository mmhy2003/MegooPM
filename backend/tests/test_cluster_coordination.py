"""HA coordination tests (MEG-35): config-version tracking + cross-node locks.

These exercise the propagation source-of-truth (:mod:`app.services.cluster`)
without Postgres: the version helpers run against a sync SQLite engine, and the
locks are tested through their OS-file-lock fallback (the non-Postgres path),
which is what proves the *mutual-exclusion* contract the advisory locks provide
in production. Cross-thread contention on separate file descriptors mirrors the
cross-node contention the advisory lock guards against.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from app.models.cluster_state import ClusterState
from app.services.cluster.locks import apply_lock, leader_lock
from app.services.cluster.version import (
    UNKNOWN_LOCAL_VERSION,
    bump_config_version,
    read_config_version,
    read_local_version,
    write_local_version,
)
from sqlalchemy import create_engine, select


def _engine(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'cluster.db'}", future=True)
    ClusterState.__table__.create(engine)
    return engine


# --- version tracking -------------------------------------------------------


def test_version_starts_at_zero_and_bumps_monotonically(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        assert read_config_version(conn) == 0
        assert bump_config_version(conn, node_id="node-a") == 1
        assert bump_config_version(conn, node_id="node-b") == 2
        assert bump_config_version(conn, node_id="node-a") == 3
        assert read_config_version(conn) == 3


def test_bump_stamps_updating_node(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        bump_config_version(conn, node_id="node-xyz")
        updated_by = conn.execute(select(ClusterState.__table__.c.updated_by)).scalar_one()
    assert updated_by == "node-xyz"


def test_local_marker_roundtrip_and_default(tmp_path: Path) -> None:
    marker = tmp_path / "nginx-config.version"
    # A fresh node (no marker) reads below the seeded 0 so it always reloads once.
    assert read_local_version(marker) == UNKNOWN_LOCAL_VERSION
    write_local_version(marker, 7)
    assert read_local_version(marker) == 7
    # No stray temp file left behind by the atomic write.
    assert not (tmp_path / ".nginx-config.version.tmp").exists()


# --- cross-node locking (file-lock fallback path) ---------------------------


def test_apply_lock_is_mutually_exclusive_across_threads(tmp_path: Path) -> None:
    """Two 'nodes' contending for the apply lock never run the critical section
    concurrently — the guarantee that stops two nodes half-writing conf.d."""
    engine = _engine(tmp_path)
    lock_file = tmp_path / "apply.lock"
    guard = threading.Lock()
    state = {"current": 0, "max": 0}

    def worker() -> None:
        with apply_lock(engine, lock_file=lock_file):
            with guard:
                state["current"] += 1
                state["max"] = max(state["max"], state["current"])
            time.sleep(0.02)
            with guard:
                state["current"] -= 1

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert state["max"] == 1  # never two holders at once


def test_apply_lock_yields_usable_connection_for_bump(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    lock_file = tmp_path / "apply.lock"
    with apply_lock(engine, lock_file=lock_file) as conn:
        version = bump_config_version(conn, node_id="node-a")
    assert version == 1
    # Committed on lock release: a fresh connection sees it.
    with engine.connect() as conn:
        assert read_config_version(conn) == 1


def test_leader_lock_grants_one_holder(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    lock_file = tmp_path / "leader.lock"
    with leader_lock(engine, "sweep", lock_file=lock_file) as first:
        assert first is True
        # A second node trying the same lock (separate fd) is refused.
        with leader_lock(engine, "sweep", lock_file=lock_file) as second:
            assert second is False
    # Released: a later attempt succeeds again.
    with leader_lock(engine, "sweep", lock_file=lock_file) as third:
        assert third is True
