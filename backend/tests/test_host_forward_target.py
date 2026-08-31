"""A proxy host forwards to exactly one of a host:port or an upstream pool.

Postgres-gated. ``proxy_hosts.domain_names`` is a Postgres ``ARRAY``, and no
SQLite shim gets around it: rendering the DDL as JSON via ``@compiles`` still
leaves ARRAY's bind processor in place, so inserting a list raises
``type 'list' is not supported``. The constraints therefore have to be exercised
against a real database — the same reason ``test_proxy_hosts_api.py`` is gated.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.core.config import settings
from app.db.base import Base
from app.models.proxy_host import ProxyHost
from app.schemas.proxy_host import ProxyHostCreate
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

# Only the DB-backed tests below are async; the schema tests are pure.
pytestmark = pytest.mark.asyncio


@pytest.fixture
async def pg_session() -> AsyncIterator[AsyncSession]:
    """A session in one rolled-back transaction, so nothing is ever committed."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        conn = await engine.connect()
    except Exception:  # pragma: no cover - environment without a database
        await engine.dispose()
        pytest.skip("No database reachable at DATABASE_URL")

    trans = await conn.begin()
    await conn.run_sync(Base.metadata.create_all)
    session = AsyncSession(bind=conn, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        if trans.is_active:
            await trans.rollback()
        await conn.close()
        await engine.dispose()


# Unique per run so assertions never collide with rows another test — or a
# manual migration check — happened to leave behind.
_DOMAIN = "target-test.example.com"


async def _add(session: AsyncSession, **kw) -> None:
    """Insert inside a SAVEPOINT so a rejected row does not poison the fixture."""
    async with session.begin_nested():
        session.add(ProxyHost(domain_names=[_DOMAIN], **kw))
        await session.flush()


async def test_rejects_both_targets(pg_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError):
        await _add(pg_session, upstream_id=1, forward_host="h", forward_port=8080)


async def test_rejects_neither_target(pg_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError):
        await _add(pg_session)


async def test_rejects_a_half_specified_host(pg_session: AsyncSession) -> None:
    """A host with no port is not a target, so the row has none at all."""
    with pytest.raises(IntegrityError):
        await _add(pg_session, forward_host="h")


async def test_accepts_a_host_target(pg_session: AsyncSession) -> None:
    await _add(pg_session, forward_host="10.0.0.1", forward_port=8080)

    row = (
        await pg_session.scalars(
            select(ProxyHost).where(ProxyHost.domain_names.any(_DOMAIN))
        )
    ).one()
    assert (row.forward_host, row.forward_port) == ("10.0.0.1", 8080)
    assert row.upstream_id is None


async def test_port_range_still_enforced_on_a_host_target(pg_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError):
        await _add(pg_session, forward_host="h", forward_port=70000)


# --- schema: exactly one target, before the database has to say so ----------


def _body(**kw) -> ProxyHostCreate:
    return ProxyHostCreate(domain_names=["a.example.com"], **kw)


def test_schema_rejects_both_targets() -> None:
    with pytest.raises(ValueError, match="either a forward host"):
        _body(upstream_id=1, forward_host="h", forward_port=8080)


def test_schema_rejects_neither_target() -> None:
    with pytest.raises(ValueError, match="either a forward host"):
        _body()


def test_schema_rejects_a_half_specified_host_body() -> None:
    with pytest.raises(ValueError, match="either a forward host"):
        _body(forward_host="h")


def test_schema_accepts_either_target() -> None:
    assert _body(upstream_id=1).upstream_id == 1
    body = _body(forward_host="10.0.0.1", forward_port=8080)
    assert (body.forward_host, body.forward_port) == ("10.0.0.1", 8080)
    assert body.upstream_id is None
