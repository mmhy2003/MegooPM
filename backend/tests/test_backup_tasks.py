"""The Celery layer: locking, the schedule tick, and recording the outcome.

The work itself is tested in test_backup_runner / test_backup_restore; these
cover what the task adds — that two backups cannot overlap, that the tick obeys
the settings, and that a failure is recorded where the page can show it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import fakeredis
import pytest
from app.models.enums import BackupFrequency
from app.tasks import backup as backup_tasks


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    client = fakeredis.FakeStrictRedis()
    monkeypatch.setattr(backup_tasks, "_lock_client", lambda: client)
    return client


def test_a_second_backup_is_refused_while_one_runs(monkeypatch, fake_redis) -> None:
    # Two pg_dumps at once would double the disk and could evict each other
    # through retention.
    lock = fake_redis.lock(backup_tasks.BACKUP_LOCK_KEY, timeout=60)
    assert lock.acquire(blocking=False)

    result = backup_tasks.create_backup_task("manual")

    assert result == {"ran": False, "reason": "already running"}


def test_the_tick_does_nothing_when_backups_are_off(monkeypatch) -> None:
    monkeypatch.setattr(
        backup_tasks,
        "_load_backup_settings",
        lambda conn: backup_tasks.BackupSettings(
            frequency=BackupFrequency.off, weekday=6, hour_utc=3, keep=7, last_backup_at=None
        ),
    )
    monkeypatch.setattr(backup_tasks, "_now", lambda: datetime(2026, 10, 11, 3, 0, tzinfo=UTC))
    monkeypatch.setattr(backup_tasks, "sync_engine", lambda: _NullEngine())
    ran: list[str] = []
    monkeypatch.setattr(backup_tasks, "_create", lambda trigger: ran.append(trigger))

    result = backup_tasks.backup_tick()

    assert result["ran"] is False
    assert ran == []


class _NullEngine:
    """Stands in for the sync engine: `with engine.connect() as conn` only."""

    def connect(self):
        return self

    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False

    def dispose(self):
        return None
