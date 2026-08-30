"""HA reload/propagation task tests (MEG-35).

Exercises the full HA write + propagation path in Celery eager mode against a
sync SQLite engine and a temp ``conf.d`` — no Postgres, no nginx, no broker:

* ``reload_nginx_config`` (HA mode) writes files, bumps the shared version,
  records the node-local marker, and pushes a reconcile to each live peer's own
  queue.
* ``reconcile_local_nginx`` reloads a node's nginx iff the shared version is
  ahead of that node's marker — the mechanism that makes a change on node A land
  on node B — and records this node's position in the node registry.
"""

from __future__ import annotations

from pathlib import Path

import app.tasks.nginx as nginx_task
import pytest
from app.core.celery_app import node_queue
from app.core.config import settings
from app.models.cluster_state import ClusterNode, ClusterState
from app.services.cluster.nodes import node_states, record_node_state
from app.services.nginx.controller import CommandResult
from app.services.nginx.state import (
    BackendSpec,
    DesiredState,
    ProxyHostSpec,
    UpstreamSpec,
)
from sqlalchemy import create_engine


class _FakeController:
    def __init__(self, test_ok: bool = True, reload_ok: bool = True) -> None:
        self.test_ok = test_ok
        self.reload_ok = reload_ok
        self.reloads = 0

    def test(self) -> CommandResult:
        return CommandResult(ok=self.test_ok, output="ok" if self.test_ok else "bad")

    def reload(self) -> CommandResult:
        self.reloads += 1
        return CommandResult(ok=self.reload_ok, output="reloaded")


def _state() -> DesiredState:
    pool = UpstreamSpec(id=1, name="p", backends=(BackendSpec(host="10.0.0.1", port=80),))
    return DesiredState(
        proxy_hosts=(ProxyHostSpec(id=1, domain_names=("x.example.com",), upstream_id=1),),
        upstreams=(pool,),
    )


@pytest.fixture
def ha_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Wire the HA task path to a temp conf.d, temp marker, and SQLite engine."""
    engine = create_engine(f"sqlite:///{tmp_path / 'cluster.db'}", future=True)
    ClusterState.__table__.create(engine)
    ClusterNode.__table__.create(engine)

    confd = tmp_path / "conf.d"
    marker = tmp_path / "node-a" / "nginx-config.version"

    monkeypatch.setattr(settings, "ha_enabled", True)
    monkeypatch.setattr(settings, "node_id", "node-a")
    monkeypatch.setattr(settings, "ha_lock_dir", str(tmp_path / "run"))
    monkeypatch.setattr(settings, "nginx_confd_dir", str(confd))
    monkeypatch.setattr(settings, "nginx_stream_dir", None)
    monkeypatch.setattr(settings, "nginx_reload_marker_path", str(marker))

    controller = _FakeController()
    monkeypatch.setattr(nginx_task, "sync_engine", lambda: engine)
    monkeypatch.setattr(nginx_task, "build_controller", lambda: controller)
    monkeypatch.setattr(nginx_task, "load_desired_state_sync", _state)

    return {
        "engine": engine,
        "confd": confd,
        "marker": marker,
        "controller": controller,
    }


def test_ha_reload_writes_files_bumps_version_and_marker(ha_env) -> None:
    result = nginx_task.reload_nginx_config()

    assert result["changed"] and result["reloaded"]
    assert result["config_version"] == 1
    assert (ha_env["confd"] / "megoopm-proxy-1.conf").exists()
    # This node recorded that it has reloaded for version 1.
    assert ha_env["marker"].read_text() == "1"


def test_ha_reload_is_idempotent_no_version_bump(ha_env) -> None:
    nginx_task.reload_nginx_config()  # version -> 1
    result = nginx_task.reload_nginx_config()  # unchanged

    assert not result["changed"]
    # Unchanged apply must not advance the shared version.
    assert result["config_version"] == 1


def test_reconcile_reloads_when_shared_version_is_ahead(ha_env, monkeypatch) -> None:
    # Node A applies the change and reloads (marker -> 1).
    nginx_task.reload_nginx_config()

    # Simulate node B: same shared engine/config, but its local marker is behind.
    node_b_marker = ha_env["marker"].parent.parent / "node-b" / "nginx-config.version"
    monkeypatch.setattr(settings, "nginx_reload_marker_path", str(node_b_marker))
    node_b_controller = _FakeController()
    monkeypatch.setattr(nginx_task, "build_controller", lambda: node_b_controller)

    result = nginx_task.reconcile_local_nginx()

    assert result["reloaded"] is True
    assert result["version"] == 1
    assert node_b_controller.reloads == 1
    assert node_b_marker.read_text() == "1"


def test_reconcile_is_noop_when_current(ha_env) -> None:
    nginx_task.reload_nginx_config()  # marker -> 1, version -> 1
    # The applying node reconciling again: already current, no reload.
    controller = ha_env["controller"]
    reloads_before = controller.reloads
    result = nginx_task.reconcile_local_nginx()

    assert result["reloaded"] is False
    assert controller.reloads == reloads_before


def test_apply_records_this_node_in_the_registry(ha_env) -> None:
    """The applying node must register itself, or peers cannot push to it."""
    nginx_task.reload_nginx_config()

    with ha_env["engine"].connect() as conn:
        states = {s.node_id: s.applied_version for s in node_states(conn)}
    assert states == {"node-a": 1}


def test_apply_pushes_a_reconcile_to_every_live_peer(ha_env, monkeypatch) -> None:
    """The fan-out addresses each peer's own queue — and never this node's."""
    with ha_env["engine"].begin() as conn:
        record_node_state(conn, "node-a", 0)
        record_node_state(conn, "node-b", 0)
        record_node_state(conn, "node-c", 0)

    sent: list[str] = []
    monkeypatch.setattr(
        nginx_task.reconcile_local_nginx,
        "apply_async",
        lambda **kw: sent.append(kw["queue"]),
    )

    result = nginx_task.reload_nginx_config()

    assert result["changed"]
    assert sorted(sent) == [node_queue("node-b"), node_queue("node-c")]
    assert node_queue("node-a") not in sent
    assert result["notified"] == ["node-b", "node-c"]


def test_unchanged_apply_pushes_nothing(ha_env, monkeypatch) -> None:
    nginx_task.reload_nginx_config()
    with ha_env["engine"].begin() as conn:
        record_node_state(conn, "node-b", 1)

    sent: list[str] = []
    monkeypatch.setattr(
        nginx_task.reconcile_local_nginx,
        "apply_async",
        lambda **kw: sent.append(kw["queue"]),
    )
    result = nginx_task.reload_nginx_config()

    assert not result["changed"]
    # No config change means nothing to propagate; waking every node would be noise.
    assert sent == []


def test_reconcile_records_the_version_it_reloaded_to(ha_env, monkeypatch) -> None:
    nginx_task.reload_nginx_config()  # version -> 1, node-a marker -> 1

    node_b_marker = ha_env["marker"].parent.parent / "node-b" / "nginx-config.version"
    monkeypatch.setattr(settings, "nginx_reload_marker_path", str(node_b_marker))
    monkeypatch.setattr(settings, "node_id", "node-b")
    monkeypatch.setattr(nginx_task, "build_controller", lambda: _FakeController())

    nginx_task.reconcile_local_nginx()

    with ha_env["engine"].connect() as conn:
        states = {s.node_id: s.applied_version for s in node_states(conn)}
    assert states["node-b"] == 1


def test_reconcile_does_not_claim_convergence_when_nginx_test_fails(
    ha_env, monkeypatch
) -> None:
    """A node that could not load the new config must still report as lagging."""
    nginx_task.reload_nginx_config()  # shared version -> 1

    node_b_marker = ha_env["marker"].parent.parent / "node-b" / "nginx-config.version"
    monkeypatch.setattr(settings, "nginx_reload_marker_path", str(node_b_marker))
    monkeypatch.setattr(settings, "node_id", "node-b")
    monkeypatch.setattr(
        nginx_task, "build_controller", lambda: _FakeController(test_ok=False)
    )

    result = nginx_task.reconcile_local_nginx()

    assert result["reloaded"] is False and result["valid"] is False
    with ha_env["engine"].connect() as conn:
        states = {s.node_id: s.applied_version for s in node_states(conn)}
    # -1 = never converged. Reporting 1 here would hide a broken node.
    assert states["node-b"] == -1
