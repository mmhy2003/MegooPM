"""Node-registry tests: fan-out targets and the convergence view.

The registry is what replaced the broken Celery ``Broadcast`` fan-out: instead
of relying on a fanout exchange, the applying node reads the live peers from
here and addresses each one's own queue.
"""

from __future__ import annotations

from pathlib import Path

from app.models.cluster_state import ClusterNode
from app.services.cluster.nodes import live_peers, node_states, record_node_state
from sqlalchemy import create_engine


def _engine(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'nodes.db'}", future=True)
    ClusterNode.__table__.create(engine)
    return engine


def test_record_node_state_inserts_then_updates(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        record_node_state(conn, "node-a", 3)
    with engine.begin() as conn:
        record_node_state(conn, "node-a", 7)

    with engine.connect() as conn:
        states = node_states(conn)
    # Upsert, not append: one row per node however many times it reconciles.
    assert [(s.node_id, s.applied_version) for s in states] == [("node-a", 7)]


def test_live_peers_excludes_self(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        record_node_state(conn, "node-a", 1)
        record_node_state(conn, "node-b", 1)
        record_node_state(conn, "node-c", 0)

    with engine.connect() as conn:
        peers = live_peers(conn, exclude="node-a", max_age_seconds=999)

    # The applying node reloaded in-place; pushing to itself would be a no-op.
    assert peers == ["node-b", "node-c"]


def test_node_states_reports_lagging_nodes(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.begin() as conn:
        record_node_state(conn, "node-a", 5)
        record_node_state(conn, "node-b", 2)

    with engine.connect() as conn:
        states = {s.node_id: s.applied_version for s in node_states(conn)}

    # This is the signal the convergence endpoint surfaces: node-b is behind.
    assert states == {"node-a": 5, "node-b": 2}
