"""API tests for MEG-21: access-list CRUD, sub-resources, and attachment.

Like the other domain-schema suites these need a reachable Postgres (skipped
otherwise). Every request runs inside one outer transaction rolled back on
teardown; the nginx reload side effect is stubbed so these stay focused on the
HTTP contract and the rendered-config end-to-end path.
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
    factory = async_sessionmaker(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    async with factory() as session:
        await user_service.create_user(
            session,
            email="meg21-admin@example.com",
            password="adminpass123",
            role=UserRole.admin,
        )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "meg21-admin@example.com", "password": "adminpass123"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture
def auth(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


# --- CRUD ------------------------------------------------------------------


async def test_create_with_inline_users_and_rules(client: AsyncClient, auth) -> None:
    resp = await client.post(
        "/api/v1/access-lists",
        headers=auth,
        json={
            "name": "ops",
            "satisfy_any": True,
            "auth_users": [{"username": "alice", "password": "s3cret"}],
            "clients": [
                {"address": "10.0.0.0/8", "directive": "allow"},
                {"address": "all", "directive": "deny"},
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "ops"
    assert body["satisfy_any"] is True
    assert len(body["auth_users"]) == 1
    assert body["auth_users"][0]["username"] == "alice"
    # Password material is never returned.
    assert "password" not in body["auth_users"][0]
    assert "password_hash" not in body["auth_users"][0]
    assert {r["directive"] for r in body["client_rules"]} == {"allow", "deny"}
    assert resp.headers["X-Config-Reload-Task"] == "test-reload-task"


async def test_update_and_delete_lifecycle(client: AsyncClient, auth) -> None:
    created = await client.post("/api/v1/access-lists", headers=auth, json={"name": "a"})
    al_id = created.json()["id"]

    patched = await client.patch(
        f"/api/v1/access-lists/{al_id}", headers=auth, json={"name": "renamed", "pass_auth": True}
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "renamed"
    assert patched.json()["pass_auth"] is True

    deleted = await client.delete(f"/api/v1/access-lists/{al_id}", headers=auth)
    assert deleted.status_code == 204
    assert (await client.get(f"/api/v1/access-lists/{al_id}", headers=auth)).status_code == 404


async def test_duplicate_username_rejected(client: AsyncClient, auth) -> None:
    resp = await client.post(
        "/api/v1/access-lists",
        headers=auth,
        json={
            "name": "dup",
            "auth_users": [
                {"username": "bob", "password": "x"},
                {"username": "bob", "password": "y"},
            ],
        },
    )
    assert resp.status_code == 422  # caught by schema validator


async def test_invalid_cidr_rejected(client: AsyncClient, auth) -> None:
    resp = await client.post(
        "/api/v1/access-lists",
        headers=auth,
        json={"name": "bad", "clients": [{"address": "not-an-ip", "directive": "allow"}]},
    )
    assert resp.status_code == 422


# --- Sub-resources ---------------------------------------------------------


async def test_auth_user_subresource(client: AsyncClient, auth) -> None:
    created = await client.post("/api/v1/access-lists", headers=auth, json={"name": "s"})
    al_id = created.json()["id"]

    added = await client.post(
        f"/api/v1/access-lists/{al_id}/auth-users",
        headers=auth,
        json={"username": "carol", "password": "pw1"},
    )
    assert added.status_code == 201, added.text
    user_id = added.json()["id"]

    # Duplicate username → 409.
    dup = await client.post(
        f"/api/v1/access-lists/{al_id}/auth-users",
        headers=auth,
        json={"username": "carol", "password": "pw2"},
    )
    assert dup.status_code == 409

    # Password reset.
    reset = await client.patch(
        f"/api/v1/access-lists/{al_id}/auth-users/{user_id}",
        headers=auth,
        json={"password": "pw3"},
    )
    assert reset.status_code == 200

    removed = await client.delete(
        f"/api/v1/access-lists/{al_id}/auth-users/{user_id}", headers=auth
    )
    assert removed.status_code == 204
    fetched = await client.get(f"/api/v1/access-lists/{al_id}", headers=auth)
    assert fetched.json()["auth_users"] == []


async def test_client_rule_subresource(client: AsyncClient, auth) -> None:
    created = await client.post("/api/v1/access-lists", headers=auth, json={"name": "s"})
    al_id = created.json()["id"]

    added = await client.post(
        f"/api/v1/access-lists/{al_id}/clients",
        headers=auth,
        json={"address": "192.168.1.0/24", "directive": "allow"},
    )
    assert added.status_code == 201, added.text
    rule_id = added.json()["id"]

    patched = await client.patch(
        f"/api/v1/access-lists/{al_id}/clients/{rule_id}",
        headers=auth,
        json={"directive": "deny"},
    )
    assert patched.status_code == 200
    assert patched.json()["directive"] == "deny"

    removed = await client.delete(
        f"/api/v1/access-lists/{al_id}/clients/{rule_id}", headers=auth
    )
    assert removed.status_code == 204


# --- Attachment + end-to-end render ----------------------------------------


async def _make_pool(client: AsyncClient, auth) -> int:
    resp = await client.post(
        "/api/v1/upstreams",
        headers=auth,
        json={"name": "alpool", "backends": [{"host": "10.0.0.1", "port": 80}]},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_attached_list_enforces_auth_and_ip_in_rendered_config(
    client: AsyncClient, auth
) -> None:
    al = await client.post(
        "/api/v1/access-lists",
        headers=auth,
        json={
            "name": "guard",
            "auth_users": [{"username": "dave", "password": "hunter2"}],
            "clients": [{"address": "203.0.113.0/24", "directive": "allow"},
                        {"address": "all", "directive": "deny"}],
        },
    )
    al_id = al.json()["id"]
    pool_id = await _make_pool(client, auth)

    created = await client.post(
        "/api/v1/proxy-hosts",
        headers=auth,
        json={
            "domain_names": ["guard.example.com"],
            "upstream_id": pool_id,
            "access_list_id": al_id,
        },
    )
    assert created.status_code == 201, created.text

    preview = await client.get("/api/v1/nginx/preview", headers=auth)
    assert preview.status_code == 200, preview.text
    config = "\n".join(f["content"] for f in preview.json()["files"])

    assert 'auth_basic "guard";' in config
    assert "auth_basic_user_file" in config
    assert "allow 203.0.113.0/24;" in config
    assert "deny all;" in config
    assert "satisfy all;" in config
    # The htpasswd sidecar file is rendered with dave's apr1 hash.
    assert any(
        f["name"] == f"megoopm-access-{al_id}.htpasswd"
        and f["content"].startswith("dave:$apr1$")
        for f in preview.json()["files"]
    )


async def test_delete_list_detaches_host(client: AsyncClient, auth) -> None:
    al = await client.post("/api/v1/access-lists", headers=auth, json={"name": "temp"})
    al_id = al.json()["id"]
    pool_id = await _make_pool(client, auth)
    host = await client.post(
        "/api/v1/proxy-hosts",
        headers=auth,
        json={
            "domain_names": ["detach.example.com"],
            "upstream_id": pool_id,
            "access_list_id": al_id,
        },
    )
    host_id = host.json()["id"]

    # Deleting the list is allowed (SET NULL); the host survives, detached.
    assert (await client.delete(f"/api/v1/access-lists/{al_id}", headers=auth)).status_code == 204
    fetched = await client.get(f"/api/v1/proxy-hosts/{host_id}", headers=auth)
    assert fetched.status_code == 200
    assert fetched.json()["access_list_id"] is None


async def test_attach_unknown_list_rejected(client: AsyncClient, auth) -> None:
    pool_id = await _make_pool(client, auth)
    resp = await client.post(
        "/api/v1/proxy-hosts",
        headers=auth,
        json={
            "domain_names": ["x.example.com"],
            "upstream_id": pool_id,
            "access_list_id": 999999,
        },
    )
    assert resp.status_code == 422, resp.text


# --- RBAC ------------------------------------------------------------------


async def test_endpoints_require_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/access-lists")).status_code == 401
    assert (await client.post("/api/v1/access-lists", json={"name": "x"})).status_code == 401
