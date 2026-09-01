"""API tests for instance settings — the default site.

Like the other domain-schema suites these need a reachable Postgres (skipped
otherwise) and run inside one outer transaction rolled back on teardown.

The ``pg_conn`` fixture builds tables with ``Base.metadata.create_all``, which
does not run migrations, so the singleton row the migration seeds is inserted
here by a fixture instead.
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
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
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


@pytest.fixture(autouse=True)
async def seeded_settings(pg_conn) -> None:
    """`create_all` builds the table but does not run the migration's seed."""
    await pg_conn.execute(
        text("INSERT INTO instance_settings (id, default_site_mode) VALUES (1, 'not_found')")
    )


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
            email="settings-admin@example.com",
            password="adminpass123",
            role=UserRole.admin,
        )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "settings-admin@example.com", "password": "adminpass123"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture
def auth(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


# --- The singleton and its constraints -------------------------------------


async def test_migration_seeds_one_row_preserving_todays_behaviour(pg_conn) -> None:
    """A fresh instance must keep serving 404, not silently switch to a new page."""
    result = await pg_conn.execute(text("SELECT id, default_site_mode FROM instance_settings"))
    rows = result.all()
    assert len(rows) == 1
    assert rows[0].id == 1
    assert rows[0].default_site_mode == "not_found"


async def test_redirect_without_a_url_is_rejected_by_the_database(pg_conn) -> None:
    """A half-configured row would render a config that says nothing."""
    with pytest.raises(IntegrityError):
        await pg_conn.execute(
            text(
                "UPDATE instance_settings SET default_site_mode = 'redirect', "
                "default_site_redirect_url = NULL WHERE id = 1"
            )
        )


# --- Routes ----------------------------------------------------------------

CUSTOM_HTML = "<!doctype html><html><body>ban</body></html>"


async def _make_page(client: AsyncClient, auth, name: str = "Denied") -> int:
    resp = await client.post(
        "/api/v1/custom-pages", headers=auth, json={"name": name, "html": CUSTOM_HTML}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_get_returns_the_seeded_default(client: AsyncClient, auth) -> None:
    resp = await client.get("/api/v1/settings", headers=auth)
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_site_mode"] == "not_found"
    assert resp.json()["default_site_redirect_url"] is None


@pytest.mark.parametrize("mode", ["congratulations", "not_found", "no_response"])
async def test_simple_modes_round_trip(client: AsyncClient, auth, mode: str) -> None:
    resp = await client.patch("/api/v1/settings", headers=auth, json={"default_site_mode": mode})
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_site_mode"] == mode


async def test_redirect_mode_round_trips(client: AsyncClient, auth) -> None:
    resp = await client.patch(
        "/api/v1/settings",
        headers=auth,
        json={
            "default_site_mode": "redirect",
            "default_site_redirect_url": "https://example.com/moved",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_site_redirect_url"] == "https://example.com/moved"


async def test_custom_page_mode_round_trips(client: AsyncClient, auth) -> None:
    page_id = await _make_page(client, auth)
    resp = await client.patch(
        "/api/v1/settings",
        headers=auth,
        json={"default_site_mode": "custom_page", "default_site_page_id": page_id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_site_page_id"] == page_id


async def test_switching_mode_clears_the_previous_mode_field(client: AsyncClient, auth) -> None:
    """A stale URL would reappear in the form if the operator switched back."""
    await client.patch(
        "/api/v1/settings",
        headers=auth,
        json={
            "default_site_mode": "redirect",
            "default_site_redirect_url": "https://example.com",
        },
    )
    resp = await client.patch(
        "/api/v1/settings", headers=auth, json={"default_site_mode": "not_found"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_site_redirect_url"] is None


async def test_incoherent_payloads_are_rejected(client: AsyncClient, auth) -> None:
    for body in (
        {"default_site_mode": "redirect"},
        {"default_site_mode": "custom_page"},
        {"default_site_mode": "redirect", "default_site_redirect_url": "not-a-url"},
    ):
        resp = await client.patch("/api/v1/settings", headers=auth, json=body)
        assert resp.status_code == 422, (body, resp.text)


async def test_unknown_page_is_rejected(client: AsyncClient, auth) -> None:
    resp = await client.patch(
        "/api/v1/settings",
        headers=auth,
        json={"default_site_mode": "custom_page", "default_site_page_id": 9999},
    )
    assert resp.status_code == 422, resp.text


async def test_a_write_enqueues_exactly_one_reload(client: AsyncClient, auth, monkeypatch) -> None:
    calls = 0

    def _counting_reload() -> TaskEnqueued:
        nonlocal calls
        calls += 1
        return TaskEnqueued(task_id="test-reload-task", status="PENDING")

    monkeypatch.setattr(config_writes, "enqueue_nginx_reload", _counting_reload)

    resp = await client.patch(
        "/api/v1/settings", headers=auth, json={"default_site_mode": "no_response"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["X-Config-Reload-Task"] == "test-reload-task"
    assert calls == 1


async def test_endpoints_require_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/settings")).status_code == 401
    assert (
        await client.patch("/api/v1/settings", json={"default_site_mode": "not_found"})
    ).status_code == 401


async def test_the_default_site_renders_the_referenced_page(
    client: AsyncClient, auth, pg_conn
) -> None:
    """End to end: setting -> loader -> renderer, with the page's own HTML."""
    from app.services.nginx.loader import load_desired_state
    from app.services.nginx.renderer import DEFAULT_SITE_HTML, render_default_site

    page_id = await _make_page(client, auth, name="Rendered")
    await client.patch(
        "/api/v1/settings",
        headers=auth,
        json={"default_site_mode": "custom_page", "default_site_page_id": page_id},
    )

    factory = async_sessionmaker(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    async with factory() as session:
        state = await load_desired_state(session)

    assert state.default_site is not None
    assert state.default_site.mode == "custom_page"
    assert render_default_site(state)[DEFAULT_SITE_HTML] == CUSTOM_HTML
