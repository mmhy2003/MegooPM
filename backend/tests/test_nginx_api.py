"""RBAC tests for the nginx config/reload endpoints.

Auth gating runs in dependencies before the handler body, so these assert the
admin-only contract without needing the Postgres-only domain tables. The full
apply path is covered by ``test_nginx_task`` / ``test_nginx_engine``.
"""

from __future__ import annotations

import app.tasks.nginx as nginx_task
from app.services.nginx.controller import CommandResult
from app.services.nginx.state import (
    BackendSpec,
    DesiredState,
    ProxyHostSpec,
    UpstreamSpec,
)
from httpx import AsyncClient


class _FakeController:
    def test(self) -> CommandResult:
        return CommandResult(ok=True, output="ok")

    def reload(self) -> CommandResult:
        return CommandResult(ok=True, output="reloaded")


def _state() -> DesiredState:
    pool = UpstreamSpec(id=1, name="p", backends=(BackendSpec(host="10.0.0.1", port=80),))
    return DesiredState(
        proxy_hosts=(ProxyHostSpec(id=1, domain_names=("x.example.com",), upstream_id=1),),
        http_upstreams=(pool,),
    )


async def test_reload_requires_authentication(db_client: AsyncClient) -> None:
    resp = await db_client.post("/api/v1/nginx/reload")
    assert resp.status_code == 401


async def test_reload_forbidden_for_non_admin(db_client: AsyncClient, member_token: str) -> None:
    resp = await db_client.post(
        "/api/v1/nginx/reload", headers={"Authorization": f"Bearer {member_token}"}
    )
    assert resp.status_code == 403


async def test_preview_requires_authentication(db_client: AsyncClient) -> None:
    resp = await db_client.get("/api/v1/nginx/preview")
    assert resp.status_code == 401


async def test_reload_enqueues_task_for_admin(
    db_client: AsyncClient, admin_token: str, monkeypatch, tmp_path
) -> None:
    # Celery runs eager in tests: patch the task internals so the inline run
    # succeeds against a temp conf.d instead of a real DB/nginx.
    monkeypatch.setattr(nginx_task, "load_desired_state_sync", _state)
    monkeypatch.setattr(nginx_task, "build_controller", lambda: _FakeController())
    monkeypatch.setattr(nginx_task.settings, "nginx_confd_dir", str(tmp_path))
    monkeypatch.setattr(nginx_task.settings, "nginx_stream_dir", str(tmp_path / "stream"))

    resp = await db_client.post(
        "/api/v1/nginx/reload", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 202
    assert resp.json()["task_id"]
