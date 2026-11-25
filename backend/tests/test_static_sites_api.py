"""Admin API for static sites against a real Postgres.

Isolation and the stubbed reload follow tests/test_meg24_api.py: one outer
transaction rolled back on teardown, ``enqueue_nginx_reload`` replaced. The
sites mount is a tmp_path with one site, ``company``, holding an index.html.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import app.api.routes._config_writes as config_writes
import pytest
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.audit_log import AuditLog
from app.models.user import UserRole
from app.schemas.tasks import TaskEnqueued
from app.services import user as user_service
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

BASE = "/api/v1/static-sites"


@pytest.fixture
def sites_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "sites"
    (root / "company").mkdir(parents=True)
    (root / "company" / "index.html").write_text("<h1>company</h1>")
    monkeypatch.setattr(settings, "sites_root", str(root))
    return root


@pytest.fixture
async def pg_conn() -> AsyncIterator:
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
def factory(pg_conn) -> async_sessionmaker:
    return async_sessionmaker(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )


@pytest.fixture
async def client(factory, monkeypatch, sites_root) -> AsyncIterator[AsyncClient]:
    async def _override_get_session() -> AsyncIterator:
        async with factory() as session:
            yield session

    monkeypatch.setattr(
        config_writes,
        "enqueue_nginx_reload",
        lambda: TaskEnqueued(task_id="test-reload-task", status="PENDING"),
    )
    app.dependency_overrides[get_session] = _override_get_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_session, None)


async def _token(client: AsyncClient, factory, email: str, role: UserRole) -> dict[str, str]:
    async with factory() as session:
        await user_service.create_user(session, email=email, password="pass12345678", role=role)
    login = {"email": email, "password": "pass12345678"}
    resp = await client.post("/api/v1/auth/login", json=login)
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def auth(client: AsyncClient, factory) -> dict[str, str]:
    return await _token(client, factory, "static-admin@example.com", UserRole.admin)


async def _create(client: AsyncClient, auth, **overrides: object):
    body: dict[str, object] = {"domain_names": ["Site.Example.com"], "folder": "company"}
    body.update(overrides)
    return await client.post(BASE, headers=auth, json=body)


async def test_create_serves_the_folder_with_crowdsec_on(client: AsyncClient, auth) -> None:
    resp = await _create(client, auth)

    assert resp.status_code == 201, resp.text
    site = resp.json()
    assert site["domain_names"] == ["site.example.com"]
    assert site["folder"] == "company"
    assert site["folder_status"] == "ok"
    assert site["crowdsec_enabled"] is True
    assert resp.headers["X-Config-Reload-Task"] == "test-reload-task"


async def test_a_missing_folder_saves_with_a_warning(client: AsyncClient, auth) -> None:
    resp = await _create(client, auth, folder="not-uploaded-yet")

    assert resp.status_code == 201, resp.text
    assert resp.json()["folder_status"] == "missing"


async def test_an_escaping_folder_is_refused(client: AsyncClient, auth) -> None:
    resp = await _create(client, auth, folder="../data")
    assert resp.status_code == 422


async def test_list_and_get_report_folder_status(client: AsyncClient, auth, sites_root) -> None:
    site_id = (await _create(client, auth)).json()["id"]
    (sites_root / "company" / "index.html").unlink()

    listed = await client.get(BASE, headers=auth)
    got = await client.get(f"{BASE}/{site_id}", headers=auth)

    assert [s["folder_status"] for s in listed.json()] == ["no_index"]
    assert got.json()["folder_status"] == "no_index"


async def test_patch_folder_is_audited(client: AsyncClient, auth, factory, sites_root) -> None:
    (sites_root / "other").mkdir()
    site_id = (await _create(client, auth)).json()["id"]

    patched = await client.patch(f"{BASE}/{site_id}", headers=auth, json={"folder": "other"})

    assert patched.status_code == 200, patched.text
    assert patched.json()["folder"] == "other"
    assert patched.json()["folder_status"] == "no_index"
    async with factory() as session:
        entries = (
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.object_type == "static_site", AuditLog.object_id == site_id
                )
            )
        ).all()
    assert [e.meta.get("changed") for e in entries if e.action == "update"] == [["folder"]]


async def test_patch_refuses_an_escaping_folder(client: AsyncClient, auth) -> None:
    site_id = (await _create(client, auth)).json()["id"]
    resp = await client.patch(f"{BASE}/{site_id}", headers=auth, json={"folder": ".git"})
    assert resp.status_code == 422


async def test_delete_then_gone(client: AsyncClient, auth) -> None:
    site_id = (await _create(client, auth)).json()["id"]

    deleted = await client.delete(f"{BASE}/{site_id}", headers=auth)
    gone = await client.get(f"{BASE}/{site_id}", headers=auth)

    assert deleted.status_code == 204
    assert deleted.headers["X-Config-Reload-Task"] == "test-reload-task"
    assert gone.status_code == 404
    assert gone.json()["detail"] == "Static site not found"


async def test_unknown_certificate_or_access_list_is_422(client: AsyncClient, auth) -> None:
    assert (await _create(client, auth, certificate_id=999999)).status_code == 422
    assert (await _create(client, auth, access_list_id=999999)).status_code == 422


async def test_deleting_the_access_list_unlinks_it(client: AsyncClient, auth) -> None:
    al = await client.post("/api/v1/access-lists", headers=auth, json={"name": "team"})
    al_id = al.json()["id"]
    site_id = (await _create(client, auth, access_list_id=al_id)).json()["id"]

    assert (await client.delete(f"/api/v1/access-lists/{al_id}", headers=auth)).status_code == 204

    got = await client.get(f"{BASE}/{site_id}", headers=auth)
    assert got.json()["access_list_id"] is None


async def test_folders_lists_the_mount(client: AsyncClient, auth) -> None:
    resp = await client.get(f"{BASE}/folders", headers=auth)

    assert resp.status_code == 200
    assert resp.json() == {
        "available": True,
        "reason": None,
        "folders": [{"path": "company", "has_index": True, "readable": True}],
    }


async def test_folders_when_nothing_is_mounted(client: AsyncClient, auth, monkeypatch) -> None:
    monkeypatch.setattr(settings, "sites_root", "/definitely/not/mounted")

    resp = await client.get(f"{BASE}/folders", headers=auth)

    assert resp.status_code == 200
    assert resp.json()["available"] is False
    assert resp.json()["reason"] == "not_mounted"


async def test_members_are_forbidden(client: AsyncClient, factory) -> None:
    member = await _token(client, factory, "static-member@example.com", UserRole.member)

    assert (await client.get(BASE, headers=member)).status_code == 403
    assert (await client.get(f"{BASE}/folders", headers=member)).status_code == 403
    assert (await _create(client, member)).status_code == 403


async def test_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get(BASE)).status_code == 401
