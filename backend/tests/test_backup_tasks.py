"""The Celery layer: locking, the schedule tick, and recording the outcome.

The work itself is tested in test_backup_runner / test_backup_restore; these
cover what the task adds — that two backups cannot overlap, that the tick obeys
the settings, and that a failure is recorded where the page can show it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

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


def test_a_restore_that_fails_after_the_swap_still_resets_the_api_pool(
    monkeypatch, fake_redis
) -> None:
    """The marker is what makes the API drop connections whose plans are void.

    On the success path it is set. If the failure happens *after* pg_restore —
    a file replacement, or the catch-up migration — the database has already
    been replaced, and without the marker the instance comes back answering
    500s with no explanation.
    """
    from app.services.backup import gate

    monkeypatch.setattr(backup_tasks.store, "safe_path", lambda d, name: Path("/backups") / name)

    def restore_then_fail(**kwargs):
        kwargs["pg"].restore(Path("/tmp/db.dump"))
        raise RuntimeError("alembic upgrade failed")

    monkeypatch.setattr(backup_tasks, "restore_backup", restore_then_fail)
    monkeypatch.setattr(
        backup_tasks,
        "real_pg_tools",
        lambda url: backup_tasks.PgTools(
            dump=lambda dst: None, restore=lambda src: None, revision=lambda: "x"
        ),
    )

    result = backup_tasks._restore("megoopm-20261011-030000-manual.mgb")

    assert result["ok"] is False
    assert gate.restored_at(fake_redis) is not None, "the API would keep its stale pool"
    assert gate.restore_status(fake_redis)["running"] is False


def test_an_unknown_backup_name_is_reported_not_swallowed(monkeypatch, fake_redis) -> None:
    # safe_path used to run before the gate was raised, so the error escaped
    # the task: the page said "Restore started" and then nothing, forever.
    from app.services.backup import gate
    from app.services.backup.store import UnknownBackupError

    def missing(directory, name):
        raise UnknownBackupError(f"no backup named {name!r}")

    monkeypatch.setattr(backup_tasks.store, "safe_path", missing)

    result = backup_tasks._restore("megoopm-20261011-030000-manual.mgb")

    assert result["ok"] is False
    assert "no backup named" in gate.restore_status(fake_redis)["error"]


def test_a_backup_refused_by_the_lock_says_so_on_the_page(monkeypatch, fake_redis) -> None:
    # 202 Accepted was already returned; if the lock then refuses the work,
    # nothing distinguished it from a backup that ran.
    recorded: list[tuple] = []
    monkeypatch.setattr(
        backup_tasks, "_record_outcome", lambda ok, message, at: recorded.append((ok, message))
    )
    lock = fake_redis.lock(backup_tasks.BACKUP_LOCK_KEY, timeout=60)
    assert lock.acquire(blocking=False)

    backup_tasks.create_backup_task("manual")

    assert recorded and recorded[0][0] is False
    assert "already running" in recorded[0][1]
