"""HA reload/propagation task tests (MEG-35).

Exercises the full HA write + propagation path in Celery eager mode against a
sync SQLite engine and a temp ``conf.d`` — no Postgres, no nginx, no broker:

* ``reload_nginx_config`` (HA mode) writes files, bumps the shared version, and
  records the node-local marker.
* ``reconcile_local_nginx`` reloads a node's nginx iff the shared version is
  ahead of that node's marker — the mechanism that makes a change on node A land
  on node B.
"""

from __future__ import annotations

from pathlib import Path

import app.tasks.nginx as nginx_task
import pytest
from app.core.config import settings
from app.models.cluster_state import ClusterState
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
