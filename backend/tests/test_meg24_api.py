"""API tests for MEG-24: streams, redirection hosts, dead (404) hosts.

These exercise the real domain schema (Postgres ``ARRAY``/``ENUM`` types), so
they need a reachable database — CI provides one via ``DATABASE_URL``; the whole
module is skipped when none is reachable, keeping the DB-less smoke suite green
everywhere.

Isolation and the stubbed reload side effect follow ``test_proxy_hosts_api``:
one outer transaction rolled back on teardown, and ``enqueue_nginx_reload``
replaced so the write path is asserted without shelling out to nginx.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import app.api.routes._config_writes as config_writes
import pytest
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.user import UserRole
from app.schemas.tasks import TaskEnqueued
from app.services import user as user_service
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


@pytest.fixture
async def pg_conn() -> AsyncIterator:
    """A single Postgres connection in one rolled-back transaction (or skip)."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        conn = await engine.connect()
    except Exception:  # pragma: no cover - environment without a database
        await engine.dispose()
        pytest.skip("No database reachable at DATABASE_URL")

    trans = await conn.begin()
    await conn.run_sync(Base.metadata.create_all)
    try:
        yield conn
    finally:
        if trans.is_active:
            await trans.rollback()
        await conn.close()
        await engine.dispose()


@pytest.fixture
async def client(pg_conn, monkeypatch) -> AsyncIterator[AsyncClient]:
    """ASGI client whose sessions join ``pg_conn`` via savepoints."""
    factory = async_sessionmaker(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )

    async def _override_get_session() -> AsyncIterator:
        async with factory() as session:
            yield session

    monkeypatch.setattr(
        config_writes,
        "enqueue_nginx_reload",
        lambda: TaskEnqueued(task_id="test-reload-task", status="PENDING"),
    )

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.fixture
async def admin_token(pg_conn, client: AsyncClient) -> str:
    """Seed an admin (in the test transaction) and return a bearer token."""
    factory = async_sessionmaker(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    async with factory() as session:
        await user_service.create_user(
            session,
            email="meg24-admin@example.com",
            password="adminpass123",
            role=UserRole.admin,
        )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "meg24-admin@example.com", "password": "adminpass123"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture
def auth(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


# --- Redirection hosts -----------------------------------------------------


async def test_redirection_host_crud_lifecycle(client: AsyncClient, auth) -> None:
    created = await client.post(
        "/api/v1/redirection-hosts",
        headers=auth,
        json={
            "domain_names": ["Old.Example.com"],
            "forward_domain_name": "New.Example.com",
            "forward_http_code": 301,
            "forward_scheme": "https",
        },
    )
    assert created.status_code == 201, created.text
    host = created.json()
    assert host["domain_names"] == ["old.example.com"]
    assert host["forward_domain_name"] == "new.example.com"
    assert host["forward_http_code"] == 301
    assert created.headers["X-Config-Reload-Task"] == "test-reload-task"
    host_id = host["id"]

    patched = await client.patch(
        f"/api/v1/redirection-hosts/{host_id}",
        headers=auth,
        json={"forward_http_code": 302, "preserve_path": False},
    )
    assert patched.status_code == 200
    assert patched.json()["forward_http_code"] == 302
    assert patched.json()["preserve_path"] is False

    deleted = await client.delete(f"/api/v1/redirection-hosts/{host_id}", headers=auth)
    assert deleted.status_code == 204
    gone = await client.get(f"/api/v1/redirection-hosts/{host_id}", headers=auth)
    assert gone.status_code == 404


async def test_redirection_host_rejects_bad_code(client: AsyncClient, auth) -> None:
    resp = await client.post(
        "/api/v1/redirection-hosts",
        headers=auth,
        json={
            "domain_names": ["x.example.com"],
            "forward_domain_name": "y.example.com",
            "forward_http_code": 200,
        },
    )
    assert resp.status_code == 422


# --- Dead (404) hosts ------------------------------------------------------


async def test_dead_host_crud_and_render(client: AsyncClient, auth) -> None:
    created = await client.post(
        "/api/v1/dead-hosts",
        headers=auth,
        json={"domain_names": ["parked.example.com"]},
    )
    assert created.status_code == 201, created.text
    host_id = created.json()["id"]

    preview = await client.get("/api/v1/nginx/preview", headers=auth)
    config = "\n".join(f["content"] for f in preview.json()["files"])
    assert "server_name parked.example.com;" in config
    assert "return 404;" in config

    deleted = await client.delete(f"/api/v1/dead-hosts/{host_id}", headers=auth)
    assert deleted.status_code == 204


# --- Streams (TCP/UDP) -----------------------------------------------------


async def test_stream_crud_and_render(client: AsyncClient, auth) -> None:
    created = await client.post(
        "/api/v1/streams",
        headers=auth,
        json={
            "incoming_port": 5432,
            "forward_host": "10.0.0.5",
            "forward_port": 5432,
            "udp_forwarding": True,
        },
    )
    assert created.status_code == 201, created.text
    stream = created.json()
    assert stream["incoming_port"] == 5432
    assert stream["tcp_forwarding"] is True
    assert stream["udp_forwarding"] is True
    stream_id = stream["id"]

    preview = await client.get("/api/v1/nginx/preview", headers=auth)
    config = "\n".join(f["content"] for f in preview.json()["files"])
    assert "listen 5432;" in config
    assert "listen 5432 udp;" in config
    assert "proxy_pass 10.0.0.5:5432;" in config

    patched = await client.patch(
        f"/api/v1/streams/{stream_id}", headers=auth, json={"forward_port": 6543}
    )
    assert patched.status_code == 200
    assert patched.json()["forward_port"] == 6543

    deleted = await client.delete(f"/api/v1/streams/{stream_id}", headers=auth)
    assert deleted.status_code == 204


async def test_stream_duplicate_port_conflicts(client: AsyncClient, auth) -> None:
    body = {"incoming_port": 9000, "forward_host": "10.0.0.9", "forward_port": 9000}
    first = await client.post("/api/v1/streams", headers=auth, json=body)
    assert first.status_code == 201, first.text
    second = await client.post("/api/v1/streams", headers=auth, json=body)
    assert second.status_code == 409, second.text


async def test_stream_requires_a_protocol(client: AsyncClient, auth) -> None:
    resp = await client.post(
        "/api/v1/streams",
        headers=auth,
        json={
            "incoming_port": 7000,
            "forward_host": "10.0.0.7",
            "forward_port": 7000,
            "tcp_forwarding": False,
            "udp_forwarding": False,
        },
    )
    assert resp.status_code == 422


async def test_stream_port_range_validated(client: AsyncClient, auth) -> None:
    resp = await client.post(
        "/api/v1/streams",
        headers=auth,
        json={"incoming_port": 70000, "forward_host": "x", "forward_port": 80},
    )
    assert resp.status_code == 422


# --- RBAC ------------------------------------------------------------------


async def test_endpoints_require_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/streams")).status_code == 401
    assert (await client.get("/api/v1/redirection-hosts")).status_code == 401
    assert (await client.get("/api/v1/dead-hosts")).status_code == 401
