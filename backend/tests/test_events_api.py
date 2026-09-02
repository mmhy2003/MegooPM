"""The event stream endpoint and its auth.

**Why the streaming cases do not go through the HTTP client.** httpx's
ASGITransport buffers a whole response before returning it, so an endpoint that
never completes hangs the test runner forever. The stream generator and the
auth dependency are therefore exercised directly, which also reaches further:
the round-trip test below proves a published event actually arrives in a frame,
which an HTTP-level test could not observe at all.

The two cases that DO complete normally — a refusal, and a request to another
route — still go through the client, because those are the ones where the wiring
is what matters.

Skipped without Postgres.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

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
async def session_factory(pg_conn):
    return async_sessionmaker(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )


@pytest.fixture
async def client(pg_conn, session_factory) -> AsyncIterator[AsyncClient]:
    async def _override_get_session() -> AsyncIterator:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.fixture
async def admin_token(session_factory, client: AsyncClient) -> str:
    async with session_factory() as session:
        await user_service.create_user(
            session,
            email="events-admin@example.com",
            password="adminpass123",
            role=UserRole.admin,
        )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "events-admin@example.com", "password": "adminpass123"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


class _FakeRequest:
    """Stands in for a Request. Only `is_disconnected` and `cookies` are read."""

    def __init__(self, *, disconnect_after: int = 0, cookies: dict | None = None):
        self._calls = 0
        self._after = disconnect_after
        self.cookies = cookies or {}

    async def is_disconnected(self) -> bool:
        self._calls += 1
        return self._calls > self._after


# --- The stream itself -----------------------------------------------------


async def test_the_stream_opens_with_a_frame() -> None:
    """It flushes an intermediary's buffer and proves the stream is live rather
    than merely accepted."""
    from app.api.routes.events import _stream

    gen = _stream(_FakeRequest())
    try:
        first = await gen.__anext__()
    finally:
        await gen.aclose()

    assert first.startswith("data: ")
    assert "stream.open" in first
    assert first.endswith("\n\n")


async def test_a_published_event_arrives_as_a_frame() -> None:
    """End to end through Redis: publish on one side, receive a frame on the
    other. This is what an HTTP-level test could not observe."""
    from app.api.routes.events import _stream
    from app.schemas.events import Event
    from app.services.events import publish

    gen = _stream(_FakeRequest(disconnect_after=50))
    try:
        await gen.__anext__()  # the opening frame
        await asyncio.sleep(0.3)  # let the subscription register
        await publish(
            Event(type="config.changed", at=datetime.now(UTC), detail={"version": 4})
        )
        frame = await asyncio.wait_for(gen.__anext__(), timeout=5)
    except OSError:  # pragma: no cover - no Redis available
        pytest.skip("No Redis reachable at REDIS_URL")
    # NOT skipping on TimeoutError: a timeout with Redis present means the
    # event was genuinely lost, and skipping there once hid a real bug — the
    # subscription used to start only after the first frame was consumed.
    finally:
        await gen.aclose()

    assert "config.changed" in frame
    assert frame.endswith("\n\n")


async def test_the_stream_stops_when_the_client_disconnects() -> None:
    """Otherwise every closed tab leaves a task and a Redis subscription behind."""
    from app.api.routes.events import _stream

    gen = _stream(_FakeRequest(disconnect_after=0))
    await gen.__anext__()  # the opening frame, yielded before any check
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(gen.__anext__(), timeout=5)


# --- Auth ------------------------------------------------------------------


async def test_the_stream_refuses_an_anonymous_connection(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/events")
    assert resp.status_code in (401, 403)


async def test_the_dependency_accepts_a_bearer_token(
    session_factory, admin_token: str
) -> None:
    """The header must keep working: the cookie is an addition, not a swap."""
    from app.api.deps import get_stream_user

    async with session_factory() as session:
        user = await get_stream_user(_FakeRequest(), admin_token, session)
    assert user.is_admin


async def test_the_dependency_accepts_the_session_cookie(
    session_factory, admin_token: str
) -> None:
    """EventSource cannot set a header, so this is the path a browser uses."""
    from app.api.deps import get_stream_user

    request = _FakeRequest(cookies={"megoopm_session": admin_token})
    async with session_factory() as session:
        user = await get_stream_user(request, None, session)
    assert user.is_admin


async def test_the_dependency_refuses_when_neither_is_present(
    session_factory,
) -> None:
    from app.api.deps import get_stream_user
    from fastapi import HTTPException

    async with session_factory() as session:
        with pytest.raises(HTTPException):
            await get_stream_user(_FakeRequest(), None, session)


async def test_cookie_auth_did_not_leak_to_the_other_routes(
    client: AsyncClient, admin_token: str
) -> None:
    """The whole reason the fallback is a separate dependency. If this fails,
    every mutating endpoint now accepts a cookie the browser attaches
    automatically — a CSRF surface across the entire API.
    """
    client.cookies.set("megoopm_session", admin_token)
    resp = await client.get("/api/v1/dashboard/summary")
    assert resp.status_code in (401, 403)
