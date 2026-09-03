"""Shared test fixtures.

The smoke tests exercise the ASGI app in-process via httpx's ASGITransport, so
no running server or database is required for the health check.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

# Configure Celery for tests before the app (and thus the Celery app) is
# imported: run tasks inline and store their results in an in-process backend so
# they are retrievable via AsyncResult without a running Redis/worker.
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")
os.environ.setdefault("CELERY_RESULT_BACKEND", "cache+memory://")

import pytest
from app.db.session import get_session
from app.main import app
from app.models.audit_log import AuditLog
from app.models.auth_token import AuthToken
from app.models.crowdsec import CrowdSecCredential
from app.models.dns_credential import DnsProviderCredential
from app.models.user import User, UserRole
from app.services import user as user_service
from httpx import ASGITransport, AsyncClient
from sqlalchemy import BigInteger
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool


# The shared ``IdMixin`` uses a ``BigInteger`` surrogate PK. SQLite only
# autoincrements an ``INTEGER PRIMARY KEY`` (rowid alias), so under the SQLite
# test engine we render BigInteger as INTEGER. Production runs on Postgres,
# where BIGINT autoincrements normally; this rule only affects the sqlite
# dialect used in tests.
@compiles(BigInteger, "sqlite")
def _sqlite_bigint_as_integer(type_, compiler, **kw):  # noqa: ANN001, ANN202
    return "INTEGER"


# ``audit_log.meta`` is a Postgres ``JSONB`` column. SQLite has no JSONB type,
# so under the test engine we render it as ``JSON`` (which aiosqlite stores as
# TEXT and round-trips as a dict). Production remains true JSONB on Postgres.
@compiles(JSONB, "sqlite")
def _sqlite_jsonb_as_json(type_, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


@pytest.fixture(autouse=True)
def _reset_crowdsec_credential_cache() -> None:
    """Clear the process-global CrowdSec credential cache between tests."""
    from app.services.crowdsec import credentials

    credentials.invalidate_cache()
    yield
    credentials.invalidate_cache()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """An httpx client bound to the ASGI app (no network, no DB)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# --- Database-backed fixtures (auth / RBAC) --------------------------------
#
# Auth tests need a real (async) database. We use an in-memory SQLite database
# shared across connections via ``StaticPool`` and create only the ``users``
# table (the wider domain schema uses Postgres-only types). The app's
# ``get_session`` dependency is overridden to bind to this engine.


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    """A sessionmaker bound to a fresh in-memory SQLite database per test."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(
            User.metadata.create_all,
            tables=[
                User.__table__,
                AuditLog.__table__,
                AuthToken.__table__,
                CrowdSecCredential.__table__,
                DnsProviderCredential.__table__,
            ],
        )

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def db_client(session_factory: async_sessionmaker) -> AsyncIterator[AsyncClient]:
    """An httpx client whose requests use the in-memory test database."""

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
async def admin_user(session_factory: async_sessionmaker) -> User:
    """A seeded active admin (password: ``adminpass123``)."""
    async with session_factory() as session:
        return await user_service.create_user(
            session,
            email="admin@example.com",
            password="adminpass123",
            full_name="Admin User",
            role=UserRole.admin,
        )


@pytest.fixture
async def member_user(session_factory: async_sessionmaker) -> User:
    """A seeded active limited member (password: ``memberpass123``)."""
    async with session_factory() as session:
        return await user_service.create_user(
            session,
            email="member@example.com",
            password="memberpass123",
            full_name="Member User",
            role=UserRole.member,
        )


async def _login(client: AsyncClient, email: str, password: str) -> str:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture
async def admin_token(db_client: AsyncClient, admin_user: User) -> str:
    """A valid access token for the seeded admin."""
    return await _login(db_client, admin_user.email, "adminpass123")


@pytest.fixture
async def member_token(db_client: AsyncClient, member_user: User) -> str:
    """A valid access token for the seeded member."""
    return await _login(db_client, member_user.email, "memberpass123")
