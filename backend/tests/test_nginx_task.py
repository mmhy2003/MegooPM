"""Tests for the async nginx reload Celery task.

Runs in Celery eager mode (configured in conftest). The task's DB loader and
nginx controller are patched so the enqueue → execute → result path is
exercised end to end against a temp ``conf.d`` — no Postgres, no nginx.
"""

from __future__ import annotations

from pathlib import Path

import app.tasks.nginx as nginx_task
from app.services.nginx.controller import CommandResult
from app.services.nginx.state import (
    BackendSpec,
    DesiredState,
    ProxyHostSpec,
    UpstreamSpec,
)


class _FakeController:
    def __init__(self, test_ok: bool = True) -> None:
        self.test_ok = test_ok

    def test(self) -> CommandResult:
        return CommandResult(ok=self.test_ok, output="ok" if self.test_ok else "bad")

    def reload(self) -> CommandResult:
        return CommandResult(ok=True, output="reloaded")


def _state() -> DesiredState:
    pool = UpstreamSpec(id=1, name="p", backends=(BackendSpec(host="10.0.0.1", port=80),))
    return DesiredState(
        proxy_hosts=(ProxyHostSpec(id=1, domain_names=("x.example.com",), upstream_id=1),),
        http_upstreams=(pool,),
    )


def _patch(monkeypatch, tmp_path: Path, *, test_ok: bool = True) -> None:
    monkeypatch.setattr(nginx_task, "load_desired_state_sync", _state)
    monkeypatch.setattr(nginx_task, "build_controller", lambda: _FakeController(test_ok=test_ok))
    monkeypatch.setattr(nginx_task.settings, "nginx_confd_dir", str(tmp_path))
    # Streams reconcile into a separate dir; keep it under the temp tree too.
    monkeypatch.setattr(nginx_task.settings, "nginx_stream_dir", str(tmp_path / "stream"))


def test_reload_task_applies_and_returns_result(monkeypatch, tmp_path: Path) -> None:
    _patch(monkeypatch, tmp_path)

    result = nginx_task.reload_nginx_config.delay().get(timeout=5)

    assert result["changed"] is True
    assert result["reloaded"] is True
    assert result["valid"] is True
    assert "megoopm-proxy-1.conf" in result["managed_files"]
    assert (tmp_path / "megoopm-proxy-1.conf").exists()


def test_reload_task_reports_rollback_on_invalid_config(monkeypatch, tmp_path: Path) -> None:
    _patch(monkeypatch, tmp_path, test_ok=False)

    result = nginx_task.reload_nginx_config.delay().get(timeout=5)

    assert result["valid"] is False
    assert result["rolled_back"] is True
    assert result["reloaded"] is False
    # Nothing left behind from the rejected config.
    assert not (tmp_path / "megoopm-proxy-1.conf").exists()
