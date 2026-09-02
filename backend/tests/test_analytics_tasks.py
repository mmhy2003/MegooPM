"""The flush and prune tasks.

Redis is stubbed. What matters here is the drain ordering — write first, remove
second — because the reverse loses a minute of data whenever Postgres hiccups,
and because removing the whole key would discard increments that arrived while
the write was in flight.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta

import pytest

pytestmark = pytest.mark.asyncio

DAY = date(2026, 9, 2)


def _null_factory():
    """A session factory whose session is never used: upsert is stubbed."""

    @asynccontextmanager
    async def factory():
        yield object()

    return factory


class FakeRedis:
    def __init__(self, counts=None, byte_totals=None):
        self._counts = counts if counts is not None else {"1.2.3.4": "5"}
        self._bytes = byte_totals if byte_totals is not None else {"1.2.3.4": "500"}
        self.deleted: list[tuple[str, tuple[str, ...]]] = []
        self.closed = False

    async def hgetall(self, key: str):
        if ":count:" in key:
            return self._counts
        return self._bytes

    async def hdel(self, key: str, *fields: str):
        self.deleted.append((key, fields))

    async def aclose(self):
        self.closed = True


async def _noop_upsert(session, rows, *, now):
    return len(rows)


async def test_a_successful_flush_removes_only_the_fields_it_read(monkeypatch) -> None:
    """Deleting the key would discard increments that arrived mid-flush."""
    from app.tasks import analytics

    fake = FakeRedis()
    monkeypatch.setattr(analytics, "_redis", lambda: fake)
    monkeypatch.setattr(analytics, "upsert_visitors", _noop_upsert)

    written = await analytics._flush_async(day=DAY, session_factory=_null_factory())

    assert written == 1
    assert fake.deleted, "fields must be removed after a successful write"
    assert all(fields == ("1.2.3.4",) for _, fields in fake.deleted)


async def test_nothing_is_removed_when_the_write_fails(monkeypatch) -> None:
    """Removing first would lose the batch whenever the database write failed;
    leaving the counters means the next flush picks them up."""
    from app.tasks import analytics

    fake = FakeRedis()

    async def boom(session, rows, *, now):
        raise RuntimeError("database is down")

    monkeypatch.setattr(analytics, "_redis", lambda: fake)
    monkeypatch.setattr(analytics, "upsert_visitors", boom)

    with pytest.raises(RuntimeError):
        await analytics._flush_async(day=DAY, session_factory=_null_factory())

    assert fake.deleted == []


async def test_a_quiet_minute_touches_nothing(monkeypatch) -> None:
    from app.tasks import analytics

    fake = FakeRedis(counts={}, byte_totals={})
    monkeypatch.setattr(analytics, "_redis", lambda: fake)
    monkeypatch.setattr(analytics, "upsert_visitors", _noop_upsert)

    written = await analytics._flush_async(day=DAY, session_factory=_null_factory())

    assert written == 0
    assert fake.deleted == []


async def test_the_connection_is_released_even_when_the_write_fails(
    monkeypatch,
) -> None:
    from app.tasks import analytics

    fake = FakeRedis()

    async def boom(session, rows, *, now):
        raise RuntimeError("database is down")

    monkeypatch.setattr(analytics, "_redis", lambda: fake)
    monkeypatch.setattr(analytics, "upsert_visitors", boom)

    with pytest.raises(RuntimeError):
        await analytics._flush_async(day=DAY, session_factory=_null_factory())

    assert fake.closed is True


async def test_the_prune_cutoff_keeps_exactly_the_configured_window(
    monkeypatch,
) -> None:
    """30 days means today plus the previous 29. An off-by-one here silently
    destroys a day of data that an audit would expect to exist."""
    from app.core.config import settings
    from app.tasks import analytics

    monkeypatch.setattr(settings, "visitor_retention_days", 30)
    cutoff = analytics.retention_cutoff(today=date(2026, 9, 30))
    assert cutoff == date(2026, 9, 1)


async def test_a_one_day_retention_keeps_only_today(monkeypatch) -> None:
    from app.core.config import settings
    from app.tasks import analytics

    monkeypatch.setattr(settings, "visitor_retention_days", 1)
    assert analytics.retention_cutoff(today=date(2026, 9, 30)) == date(2026, 9, 30)


def test_both_tasks_are_scheduled() -> None:
    from app.core.celery_app import celery_app

    tasks = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}
    assert "app.tasks.analytics.flush_visitor_counters" in tasks
    assert "app.tasks.analytics.prune_visitor_days" in tasks


def test_the_flush_is_not_pinned_to_one_node() -> None:
    """Unlike the metrics scrape, the counters live in a SHARED Redis, so any
    node may drain them and leader_lock decides which does. A node route would
    make the flush stop whenever that one node was down."""
    from app.core.celery_app import _configure_ha
    from celery import Celery

    app = Celery("probe")
    app.conf.beat_schedule = {}
    _configure_ha(app)

    assert "app.tasks.analytics.flush_visitor_counters" not in (
        app.conf.task_routes or {}
    )


def test_the_flush_reads_utc_days() -> None:
    """The nginx handler builds its key with os.date("!%Y-%m-%d") — UTC. A
    local-time flush would read a different key for part of each day and strand
    the counters until their TTL removed them, with no error anywhere.
    """
    from app.tasks import analytics

    days = analytics._flush_days()
    assert days[0] == datetime.now(UTC).date()


def test_the_flush_also_covers_yesterday() -> None:
    """A flush running just after midnight would otherwise abandon the previous
    day's final minute of traffic."""
    from app.tasks import analytics

    days = analytics._flush_days()
    assert days[1] == days[0] - timedelta(days=1)
