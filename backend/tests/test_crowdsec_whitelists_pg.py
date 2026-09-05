"""CrowdSec whitelist persistence against Postgres (skipped if unavailable).

``ips``/``cidrs`` are Postgres ``ARRAY`` columns, so the SQLite test engine
cannot exercise them: the ``@compiles`` shim fixes DDL only and the bind still
fails with "type 'list' is not supported". Runs in one rolled-back transaction,
so it never commits data or interferes with concurrent work.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.core.config import settings
from app.models.crowdsec_whitelist import CrowdSecWhitelist
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        conn = await engine.connect()
    except Exception:  # pragma: no cover - environment without a database
        await engine.dispose()
        pytest.skip("No database reachable at DATABASE_URL")

    try:
        await conn.exec_driver_sql("SELECT id FROM crowdsec_whitelists LIMIT 0")
    except Exception:  # pragma: no cover - migration 0016 not applied
        await conn.close()
        await engine.dispose()
        pytest.skip("crowdsec_whitelists table missing (migration 0016 not applied)")

    # The probe autobegan a transaction; close it before opening the one this
    # test runs in, or `begin()` raises "already initialized a Transaction".
    await conn.rollback()
    trans = await conn.begin()
    session = AsyncSession(bind=conn, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


async def test_round_trips_ip_and_cidr_arrays(session: AsyncSession) -> None:
    row = CrowdSecWhitelist(
        name="internal backends",
        reason="internal backends trip appsec generic rules",
        ips=["10.10.0.14"],
        cidrs=["10.10.0.0/24"],
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    assert row.ips == ["10.10.0.14"]
    assert row.cidrs == ["10.10.0.0/24"]
    assert row.enabled is True


async def test_rejects_a_whitelist_matching_nothing(session: AsyncSession) -> None:
    # A whitelist with no ips and no cidrs silently matches nothing; the database
    # refuses it so a caller bypassing the API cannot create one.
    session.add(CrowdSecWhitelist(name="empty", reason="nothing", ips=[], cidrs=[]))
    with pytest.raises(IntegrityError):
        await session.flush()
