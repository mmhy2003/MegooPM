"""API tests for MEG-17: proxy-host + upstream CRUD.

These exercise the real domain schema (Postgres ``ARRAY``/``ENUM`` types), so
they need a reachable database — CI provides one via ``DATABASE_URL``; the whole
module is skipped when none is reachable, keeping the DB-less smoke suite green
everywhere.

Isolation: every request runs against a single connection wrapped in one outer
transaction that is rolled back on teardown (sessions join it via savepoints, so
handler ``commit`` calls don't leak rows). The nginx reload side effect is
stubbed — the render/apply path is covered by ``test_nginx_render`` /
``test_nginx_engine`` — leaving these focused on the HTTP contract.
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
    # Ensure the schema exists (no-op when migrations already created it). DDL is
    # transactional on Postgres, so anything created here rolls back cleanly.
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

    # Stub the reload trigger: assert the write path enqueues, without shelling
    # out to nginx or spinning a second DB connection.
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
            email="meg17-admin@example.com",
            password="adminpass123",
            role=UserRole.admin,
        )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "meg17-admin@example.com", "password": "adminpass123"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture
def auth(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


# --- Upstream CRUD ---------------------------------------------------------


async def test_create_upstream_with_backends(client: AsyncClient, auth) -> None:
    resp = await client.post(
        "/api/v1/upstreams",
        headers=auth,
        json={
            "name": "web-pool",
            "lb_method": "least_conn",
            "backends": [
                {"host": "10.0.0.1", "port": 8080, "weight": 5},
                {"host": "10.0.0.2", "port": 8080},
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "web-pool"
    assert body["lb_method"] == "least_conn"
    assert len(body["backends"]) == 2
    assert {b["host"] for b in body["backends"]} == {"10.0.0.1", "10.0.0.2"}
    # The write drove a config reload (task id surfaced in the header).
    assert resp.headers["X-Config-Reload-Task"] == "test-reload-task"


async def test_duplicate_backend_rejected(client: AsyncClient, auth) -> None:
    resp = await client.post(
        "/api/v1/upstreams",
        headers=auth,
        json={
            "name": "dup-pool",
            "backends": [
                {"host": "10.0.0.1", "port": 80},
                {"host": "10.0.0.1", "port": 80},
            ],
        },
    )
    assert resp.status_code == 409, resp.text


async def test_backend_port_validation(client: AsyncClient, auth) -> None:
    resp = await client.post(
        "/api/v1/upstreams",
        headers=auth,
        json={"name": "bad", "backends": [{"host": "x", "port": 70000}]},
    )
    assert resp.status_code == 422


async def test_upstream_backend_subresource(client: AsyncClient, auth) -> None:
    created = await client.post("/api/v1/upstreams", headers=auth, json={"name": "p"})
    pool_id = created.json()["id"]

    added = await client.post(
        f"/api/v1/upstreams/{pool_id}/backends",
        headers=auth,
        json={"host": "10.0.0.9", "port": 3000, "weight": 2},
    )
    assert added.status_code == 201, added.text
    backend_id = added.json()["id"]

    patched = await client.patch(
        f"/api/v1/upstreams/{pool_id}/backends/{backend_id}",
        headers=auth,
        json={"weight": 7, "down": True},
    )
    assert patched.status_code == 200
    assert patched.json()["weight"] == 7
    assert patched.json()["down"] is True

    removed = await client.delete(
        f"/api/v1/upstreams/{pool_id}/backends/{backend_id}", headers=auth
    )
    assert removed.status_code == 204

    fetched = await client.get(f"/api/v1/upstreams/{pool_id}", headers=auth)
    assert fetched.json()["backends"] == []


# --- Proxy-host CRUD -------------------------------------------------------


async def _make_pool(client: AsyncClient, auth) -> int:
    resp = await client.post(
        "/api/v1/upstreams",
        headers=auth,
        json={"name": "hpool", "backends": [{"host": "10.0.0.1", "port": 80}]},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_proxy_host_crud_lifecycle(client: AsyncClient, auth) -> None:
    pool_id = await _make_pool(client, auth)

    created = await client.post(
        "/api/v1/proxy-hosts",
        headers=auth,
        json={
            "domain_names": ["App.Example.com", "app.example.com"],
            "upstream_id": pool_id,
            "allow_websocket_upgrade": True,
        },
    )
    assert created.status_code == 201, created.text
    host = created.json()
    # Domains normalised: trimmed, lower-cased, de-duplicated.
    assert host["domain_names"] == ["app.example.com"]
    assert host["allow_websocket_upgrade"] is True
    host_id = host["id"]

    listed = await client.get("/api/v1/proxy-hosts", headers=auth)
    assert any(h["id"] == host_id for h in listed.json())

    patched = await client.patch(
        f"/api/v1/proxy-hosts/{host_id}",
        headers=auth,
        json={"block_exploits": True, "enabled": False},
    )
    assert patched.status_code == 200
    assert patched.json()["block_exploits"] is True
    assert patched.json()["enabled"] is False

    deleted = await client.delete(f"/api/v1/proxy-hosts/{host_id}", headers=auth)
    assert deleted.status_code == 204

    gone = await client.get(f"/api/v1/proxy-hosts/{host_id}", headers=auth)
    assert gone.status_code == 404


async def test_proxy_host_rejects_unknown_upstream(client: AsyncClient, auth) -> None:
    resp = await client.post(
        "/api/v1/proxy-hosts",
        headers=auth,
        json={"domain_names": ["x.example.com"], "upstream_id": 999999},
    )
    assert resp.status_code == 422, resp.text


async def test_invalid_domain_rejected(client: AsyncClient, auth) -> None:
    pool_id = await _make_pool(client, auth)
    resp = await client.post(
        "/api/v1/proxy-hosts",
        headers=auth,
        json={"domain_names": ["not a domain"], "upstream_id": pool_id},
    )
    assert resp.status_code == 422


async def test_referenced_upstream_delete_conflicts(client: AsyncClient, auth) -> None:
    pool_id = await _make_pool(client, auth)
    await client.post(
        "/api/v1/proxy-hosts",
        headers=auth,
        json={"domain_names": ["ref.example.com"], "upstream_id": pool_id},
    )
    resp = await client.delete(f"/api/v1/upstreams/{pool_id}", headers=auth)
    assert resp.status_code == 409, resp.text


# --- End-to-end: CRUD drives the rendered nginx config ---------------------


async def test_created_host_renders_upstream_and_proxy_pass(client: AsyncClient, auth) -> None:
    """The headline path: a host + multi-backend pool renders a correct config."""
    pool = await client.post(
        "/api/v1/upstreams",
        headers=auth,
        json={
            "name": "render-pool",
            "lb_method": "least_conn",
            "backends": [
                {"host": "10.0.0.1", "port": 8080, "weight": 5, "max_fails": 3,
                 "fail_timeout_seconds": 20},
                {"host": "10.0.0.2", "port": 8080},
            ],
        },
    )
    pool_id = pool.json()["id"]
    await client.post(
        "/api/v1/proxy-hosts",
        headers=auth,
        json={"domain_names": ["render.example.com"], "upstream_id": pool_id},
    )

    preview = await client.get("/api/v1/nginx/preview", headers=auth)
    assert preview.status_code == 200, preview.text
    config = "\n".join(f["content"] for f in preview.json()["files"])

    pool_name = f"megoopm_upstream_{pool_id}"
    # A correct upstream{} block with the chosen LB method and health params.
    assert f"upstream {pool_name} {{" in config
    assert "least_conn;" in config
    assert "server 10.0.0.1:8080 weight=5 max_fails=3 fail_timeout=20s;" in config
    assert "server 10.0.0.2:8080 weight=1 max_fails=1 fail_timeout=10s;" in config
    # ...and a proxy_pass pointing the server{} block at that pool.
    assert f"proxy_pass http://{pool_name};" in config


# --- Locations (per-path routes to other pools) ----------------------------


async def _make_named_pool(client: AsyncClient, auth, name: str, backends: bool = True) -> int:
    resp = await client.post(
        "/api/v1/upstreams",
        headers=auth,
        json={
            "name": name,
            "backends": [{"host": "10.0.0.9", "port": 8080}] if backends else [],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_locations_crud_replace_in_full(client: AsyncClient, auth) -> None:
    root = await _make_pool(client, auth)
    api = await _make_named_pool(client, auth, "api-pool")
    ws = await _make_named_pool(client, auth, "ws-pool")

    created = await client.post(
        "/api/v1/proxy-hosts",
        headers=auth,
        json={
            "domain_names": ["loc.example.com"],
            "upstream_id": root,
            "locations": [{"path": "/api/", "upstream_id": api, "forward_scheme": "https"}],
        },
    )
    assert created.status_code == 201, created.text
    host = created.json()
    assert [
        (loc["path"], loc["upstream_id"], loc["forward_scheme"]) for loc in host["locations"]
    ] == [("/api/", api, "https")]
    host_id = host["id"]

    # Omitted -> unchanged.
    patched = await client.patch(
        f"/api/v1/proxy-hosts/{host_id}", headers=auth, json={"block_exploits": True}
    )
    assert patched.status_code == 200, patched.text
    assert [loc["path"] for loc in patched.json()["locations"]] == ["/api/"]

    # A list -> replaced in full (sorted by path on read).
    patched = await client.patch(
        f"/api/v1/proxy-hosts/{host_id}",
        headers=auth,
        json={
            "locations": [
                {"path": "/ws", "upstream_id": ws},
                {"path": "/admin/", "upstream_id": api},
            ]
        },
    )
    assert patched.status_code == 200, patched.text
    assert [(loc["path"], loc["upstream_id"]) for loc in patched.json()["locations"]] == [
        ("/admin/", api),
        ("/ws", ws),
    ]

    listed = await client.get("/api/v1/proxy-hosts", headers=auth)
    row = next(h for h in listed.json() if h["id"] == host_id)
    assert len(row["locations"]) == 2

    # [] -> cleared.
    patched = await client.patch(
        f"/api/v1/proxy-hosts/{host_id}", headers=auth, json={"locations": []}
    )
    assert patched.json()["locations"] == []


async def test_location_with_unknown_pool_is_rejected(client: AsyncClient, auth) -> None:
    root = await _make_pool(client, auth)
    resp = await client.post(
        "/api/v1/proxy-hosts",
        headers=auth,
        json={
            "domain_names": ["badloc.example.com"],
            "upstream_id": root,
            "locations": [{"path": "/api/", "upstream_id": 999999}],
        },
    )
    assert resp.status_code == 422, resp.text
    assert "999999" in resp.json()["detail"]


async def test_pool_used_only_by_a_location_cannot_be_deleted(client: AsyncClient, auth) -> None:
    root = await _make_pool(client, auth)
    api = await _make_named_pool(client, auth, "api-only")
    await client.post(
        "/api/v1/proxy-hosts",
        headers=auth,
        json={
            "domain_names": ["restrict.example.com"],
            "upstream_id": root,
            "locations": [{"path": "/api/", "upstream_id": api}],
        },
    )
    resp = await client.delete(f"/api/v1/upstreams/{api}", headers=auth)
    assert resp.status_code == 409, resp.text


async def test_deleting_host_cascades_locations(client: AsyncClient, auth) -> None:
    root = await _make_pool(client, auth)
    api = await _make_named_pool(client, auth, "cascade-pool")
    created = await client.post(
        "/api/v1/proxy-hosts",
        headers=auth,
        json={
            "domain_names": ["cascade.example.com"],
            "upstream_id": root,
            "locations": [{"path": "/api/", "upstream_id": api}],
        },
    )
    host_id = created.json()["id"]
    assert (await client.delete(f"/api/v1/proxy-hosts/{host_id}", headers=auth)).status_code == 204
    # The location row is gone, so the pool is deletable again.
    assert (await client.delete(f"/api/v1/upstreams/{api}", headers=auth)).status_code == 204


async def test_locations_render_in_preview_and_skip_empty_pools(client: AsyncClient, auth) -> None:
    root = await _make_pool(client, auth)
    api = await _make_named_pool(client, auth, "preview-api")
    empty = await _make_named_pool(client, auth, "preview-empty", backends=False)
    await client.post(
        "/api/v1/proxy-hosts",
        headers=auth,
        json={
            "domain_names": ["preview.example.com"],
            "upstream_id": root,
            "locations": [
                {"path": "/api/", "upstream_id": api, "forward_scheme": "https"},
                {"path": "/void/", "upstream_id": empty},
            ],
        },
    )
    preview = await client.get("/api/v1/nginx/preview", headers=auth)
    assert preview.status_code == 200, preview.text
    files = {f["name"]: f["content"] for f in preview.json()["files"]}
    config = "\n".join(files.values())
    assert f"upstream megoopm_upstream_{api} {{" in config
    assert f"proxy_pass https://megoopm_upstream_{api};" in config
    assert "location ^~ /api/ {" in config
    # A location whose pool has no backends is dropped; its pool is not emitted.
    assert "/void/" not in config
    assert f"megoopm-upstream-{empty}.conf" not in files


# --- RBAC ------------------------------------------------------------------


async def test_endpoints_require_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/upstreams")).status_code == 401
    assert (await client.get("/api/v1/proxy-hosts")).status_code == 401
    assert (await client.post("/api/v1/upstreams", json={"name": "x"})).status_code == 401
