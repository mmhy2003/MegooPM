"""The visitor upsert, against real Postgres.

No pure test can check this: the whole question is whether `ON CONFLICT` adds
or replaces, and that is decided by the database. A replacing upsert would look
correct in review and reset every visitor's count once a minute, forever.

Skipped without Postgres.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import pytest
from app.core.config import settings
from app.db.base import Base
from app.models.visitor_day import VisitorDay
from app.services.analytics.flush import VisitorCounts, upsert_visitors
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.asyncio

DAY = date(2026, 9, 2)
OTHER_DAY = date(2026, 9, 3)
NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 2, 12, 1, 0, tzinfo=UTC)


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
    # join_transaction_mode keeps the service's commit() inside this
    # transaction, so the rollback still cleans up.
    session = AsyncSession(
        bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    try:
        yield session
    finally:
        await session.close()
        if trans.is_active:
            await trans.rollback()
        await conn.close()
        await engine.dispose()


async def test_two_flushes_of_the_same_visitor_add_up(pg_session: AsyncSession) -> None:
    """The bug this guards: a replacing upsert resets every count once a minute."""
    rows = [VisitorCounts(ip="1.2.3.4", day=DAY, requests=10, bytes=100)]
    await upsert_visitors(pg_session, rows, now=NOW)
    await upsert_visitors(pg_session, rows, now=LATER)

    stored = (await pg_session.scalars(select(VisitorDay))).all()
    assert len(stored) == 1
    assert stored[0].request_count == 20
    assert stored[0].bytes == 200


async def test_first_seen_is_not_moved_by_a_later_flush(
    pg_session: AsyncSession,
) -> None:
    await upsert_visitors(
        pg_session, [VisitorCounts("1.2.3.4", DAY, 1, 1)], now=NOW
    )
    await upsert_visitors(
        pg_session, [VisitorCounts("1.2.3.4", DAY, 1, 1)], now=LATER
    )
    stored = (await pg_session.scalars(select(VisitorDay))).all()
    assert stored[0].first_seen_at == NOW
    assert stored[0].last_seen_at == LATER


async def test_the_same_ip_on_two_days_is_two_rows(pg_session: AsyncSession) -> None:
    """Daily bucketing is what makes the prune a single DELETE."""
    await upsert_visitors(pg_session, [VisitorCounts("1.2.3.4", DAY, 1, 1)], now=NOW)
    await upsert_visitors(
        pg_session, [VisitorCounts("1.2.3.4", OTHER_DAY, 1, 1)], now=NOW
    )
    assert len((await pg_session.scalars(select(VisitorDay))).all()) == 2


async def test_a_batch_of_many_visitors_writes_them_all(
    pg_session: AsyncSession,
) -> None:
    rows = [VisitorCounts(f"10.0.0.{i}", DAY, i, i * 10) for i in range(1, 21)]
    written = await upsert_visitors(pg_session, rows, now=NOW)
    assert written == 20
    assert len((await pg_session.scalars(select(VisitorDay))).all()) == 20


async def test_an_empty_batch_writes_nothing(pg_session: AsyncSession) -> None:
    """A quiet minute must not produce an empty INSERT, which Postgres rejects."""
    assert await upsert_visitors(pg_session, [], now=NOW) == 0


# --- Reading the aggregates back -------------------------------------------


async def test_visitors_are_summed_across_days_and_ranked(
    pg_session: AsyncSession,
) -> None:
    """A visitor active on several days must outrank one busy for an hour, so
    the totals have to sum across rows rather than take the latest."""
    from app.services.dashboard.visitors import load_visitors

    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)
    await upsert_visitors(
        pg_session,
        [
            VisitorCounts("1.1.1.1", today, 5, 50),
            VisitorCounts("1.1.1.1", yesterday, 7, 70),
            VisitorCounts("2.2.2.2", today, 100, 1000),
        ],
        now=NOW,
    )

    got = await load_visitors(pg_session, days=2)

    assert got.total_visitors == 2
    assert got.total_requests == 112
    # 2.2.2.2 has more requests in one day than 1.1.1.1 has across two.
    assert [row.ip for row in got.top_ips] == ["2.2.2.2", "1.1.1.1"]
    assert got.top_ips[1].requests == 12


async def test_the_window_excludes_older_days(pg_session: AsyncSession) -> None:
    from app.services.dashboard.visitors import load_visitors

    today = datetime.now(UTC).date()
    await upsert_visitors(
        pg_session,
        [
            VisitorCounts("1.1.1.1", today, 3, 30),
            VisitorCounts("9.9.9.9", today - timedelta(days=5), 99, 990),
        ],
        now=NOW,
    )

    got = await load_visitors(pg_session, days=1)

    assert got.total_visitors == 1
    assert got.total_requests == 3


async def test_an_unlocated_visitor_is_counted_but_not_in_the_countries(
    pg_session: AsyncSession,
) -> None:
    """The gap between the totals and the country breakdown IS the unlocated
    traffic; hiding the visitor entirely would understate the numbers."""
    from app.services.dashboard.visitors import load_visitors

    today = datetime.now(UTC).date()
    # 10.0.0.1 is private, so the lookup yields no country.
    await upsert_visitors(
        pg_session, [VisitorCounts("10.0.0.1", today, 4, 40)], now=NOW
    )

    got = await load_visitors(pg_session, days=1)

    assert got.total_visitors == 1
    assert got.total_requests == 4
    assert got.countries == []
    assert got.top_ips[0].country is None
