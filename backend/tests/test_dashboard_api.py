"""The dashboard summary endpoint.

Skipped without Postgres. The point of these tests is the degradation rules:
CrowdSec is unconfigured here, so every run exercises the path where one source
fails and the rest of the page must still render.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.user import UserRole
from app.services import user as user_service
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.asyncio


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
async def client(pg_conn) -> AsyncIterator[AsyncClient]:
    factory = async_sessionmaker(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )

    async def _override_get_session() -> AsyncIterator:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.fixture
async def auth(pg_conn, client: AsyncClient) -> dict[str, str]:
    factory = async_sessionmaker(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    async with factory() as session:
        await user_service.create_user(
            session,
            email="dash-admin@example.com",
            password="adminpass123",
            role=UserRole.admin,
        )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "dash-admin@example.com", "password": "adminpass123"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def test_summary_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/dashboard/summary")).status_code in (401, 403)


async def test_summary_counts_hosts_and_certificates(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    body = (await client.get("/api/v1/dashboard/summary", headers=auth)).json()
    assert body["inventory"]["proxy_hosts_total"] == 0
    assert body["certificates"]["expiring_soon"] == 0
    assert body["certificates"]["total"] == 0


async def test_summary_reports_traffic_as_unmeasured_before_any_scrape(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    """Not zero: nothing has measured this instance yet, and the card has to be
    able to say so rather than claim the server is idle."""
    body = (await client.get("/api/v1/dashboard/summary", headers=auth)).json()
    assert body["traffic"]["reporting_nodes"] == 0
    assert body["traffic"]["active_connections"] is None
    assert body["traffic"]["requests_per_second"] is None


async def test_summary_survives_crowdsec_being_unavailable(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    """The certificate card is the one that matters most; a CrowdSec outage must
    not take the page down with it."""
    resp = await client.get("/api/v1/dashboard/summary", headers=auth)
    assert resp.status_code == 200
    body = resp.json()
    assert body["security"] is None
    assert body["certificates"] is not None
    assert body["config"] is not None


async def test_summary_reports_config_health(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    body = (await client.get("/api/v1/dashboard/summary", headers=auth)).json()
    assert body["config"]["config_version"] == 0
    assert body["config"]["nodes_total"] == 0
    # No nodes registered yet, so nothing has converged onto anything.
    assert body["config"]["converged"] is False


async def test_visitors_is_empty_before_any_traffic(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    body = (await client.get("/api/v1/dashboard/visitors", headers=auth)).json()
    assert body["total_visitors"] == 0
    assert body["total_requests"] == 0
    assert body["countries"] == []
    assert body["top_ips"] == []


async def test_visitors_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/dashboard/visitors")).status_code in (401, 403)


async def test_visitors_window_is_clamped_to_the_retention_limit(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    """Asking for a year when 30 days are kept would otherwise report a
    30-day figure labelled as a year."""
    from app.core.config import settings

    body = (
        await client.get("/api/v1/dashboard/visitors?days=365", headers=auth)
    ).json()
    assert body["days"] == settings.visitor_retention_days


async def test_visitors_rejects_a_zero_day_window(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    resp = await client.get("/api/v1/dashboard/visitors?days=0", headers=auth)
    assert resp.status_code == 422
