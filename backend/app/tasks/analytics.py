"""Draining visitor counters, and enforcing how long the rows are kept.

Both tasks are cluster-wide singletons. The counters live in a **shared** Redis
(HA requires one), so every node sees the same data: without a lock, each node
would drain and upsert it and every count would be multiplied by the cluster
size. That is also why neither task is routed to a node's own queue, unlike the
nginx metrics scrape — any node may do this work, and the lock picks one.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta

import redis.asyncio as aioredis
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.visitor_day import VisitorDay
from app.services.analytics.flush import parse_counters, upsert_visitors

log = logging.getLogger(__name__)


def _redis():
    return aioredis.from_url(settings.redis_url, decode_responses=True)


def _session_factory():
    engine = create_async_engine(settings.database_url)
    return async_sessionmaker(engine, expire_on_commit=False)


def retention_cutoff(*, today: date) -> date:
    """The oldest day that is kept.

    Inclusive of today: a 30-day window keeps today and the previous 29. Using
    ``today - days`` would silently drop a day more than configured, which
    nobody notices until an audit asks how long the data is held.
    """
    return today - timedelta(days=settings.visitor_retention_days - 1)


async def _flush_async(*, day: date, session_factory=None) -> int:
    """Drain one day's counters into Postgres.

    Write first, remove second. If the database write fails the fields stay in
    Redis and the next flush picks them up; the reverse order would lose a
    minute of data every time Postgres hiccupped.
    """
    client = _redis()
    count_key = f"{settings.visitor_redis_prefix}:count:{day.isoformat()}"
    bytes_key = f"{settings.visitor_redis_prefix}:bytes:{day.isoformat()}"
    try:
        counts = await client.hgetall(count_key)
        if not counts:
            return 0
        byte_totals = await client.hgetall(bytes_key)
        rows = parse_counters(counts, byte_totals, day)
        if not rows:
            return 0

        factory = session_factory or _session_factory()
        async with factory() as session:
            written = await upsert_visitors(session, rows, now=datetime.now(UTC))

        # Only the fields just written, never the whole key: increments that
        # arrived while the write was in flight must survive.
        fields = [row.ip for row in rows]
        await client.hdel(count_key, *fields)
        await client.hdel(bytes_key, *fields)
        return written
    finally:
        await client.aclose()


async def _prune_async(*, today: date, session_factory=None) -> int:
    """Delete visitor rows past the retention window."""
    cutoff = retention_cutoff(today=today)
    factory = session_factory or _session_factory()
    async with factory() as session:
        result = await session.execute(delete(VisitorDay).where(VisitorDay.day < cutoff))
        await session.commit()
        return int(result.rowcount or 0)


def _flush_days() -> list[date]:
    """Today and yesterday, in UTC.

    UTC because the nginx handler builds its key with ``os.date("!%Y-%m-%d")``;
    a local-time flush would read a different key for part of each day and
    strand the counters until their TTL removed them.

    Yesterday too, because a flush that runs just after midnight would
    otherwise abandon the previous day's final minute of traffic.
    """
    today = datetime.now(UTC).date()
    return [today, today - timedelta(days=1)]


@celery_app.task(name="app.tasks.analytics.flush_visitor_counters")
def flush_visitor_counters() -> dict:
    """Drain the shared counters. One node at a time; see the module docstring."""
    if not settings.ha_enabled:
        written = sum(asyncio.run(_flush_async(day=day)) for day in _flush_days())
        return {"written": written}

    from app.services.cluster import leader_lock, sync_engine

    engine = sync_engine()
    try:
        lock_file = f"{settings.ha_lock_dir}/leader-visitor-flush.lock"
        with leader_lock(engine, "visitor-flush", lock_file=lock_file) as is_leader:
            if not is_leader:
                return {"skipped": True, "reason": "another node holds the flush lock"}
            written = sum(asyncio.run(_flush_async(day=day)) for day in _flush_days())
            return {"written": written}
    finally:
        engine.dispose()


@celery_app.task(name="app.tasks.analytics.prune_visitor_days")
def prune_visitor_days() -> dict:
    """Enforce the retention limit on visitor rows.

    Not optional housekeeping: these rows are IP addresses, and an analytics
    table with no expiry is a liability that grows quietly.
    """
    today = datetime.now(UTC).date()

    if not settings.ha_enabled:
        removed = asyncio.run(_prune_async(today=today))
        log.info("pruned %s visitor rows past the retention window", removed)
        return {"removed": removed}

    from app.services.cluster import leader_lock, sync_engine

    engine = sync_engine()
    try:
        lock_file = f"{settings.ha_lock_dir}/leader-visitor-prune.lock"
        with leader_lock(engine, "visitor-prune", lock_file=lock_file) as is_leader:
            if not is_leader:
                return {"skipped": True, "reason": "another node holds the prune lock"}
            removed = asyncio.run(_prune_async(today=today))
            log.info("pruned %s visitor rows past the retention window", removed)
            return {"removed": removed}
    finally:
        engine.dispose()


__all__ = ["flush_visitor_counters", "prune_visitor_days", "retention_cutoff"]
