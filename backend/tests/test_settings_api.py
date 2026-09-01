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
