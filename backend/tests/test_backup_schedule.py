"""Whether the hourly tick should take a backup.

Pure arithmetic over the settings and the clock, so the task can stay a thin
wrapper and the decision can be tested without Celery, Redis or a database.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.enums import BackupFrequency
from app.services.backup.schedule import is_backup_due

SUNDAY_3AM = datetime(2026, 10, 11, 3, 0, tzinfo=UTC)


def _due(**over) -> tuple[bool, str]:
    kwargs = {
        "now": SUNDAY_3AM,
        "frequency": BackupFrequency.daily,
        "weekday": 6,
        "hour_utc": 3,
        "last_backup_at": None,
    }
    return is_backup_due(**(kwargs | over))


def test_a_daily_backup_runs_in_its_hour() -> None:
    assert _due() == (True, "due")


def test_nothing_runs_when_automatic_backups_are_off() -> None:
    assert _due(frequency=BackupFrequency.off)[0] is False


def test_another_hour_is_not_the_hour() -> None:
    assert _due(now=SUNDAY_3AM.replace(hour=4))[0] is False


def test_a_weekly_backup_waits_for_its_weekday() -> None:
    assert _due(frequency=BackupFrequency.weekly, weekday=6)[0] is True
    assert _due(frequency=BackupFrequency.weekly, weekday=2)[0] is False


def test_it_does_not_run_twice_in_the_same_hour() -> None:
    # Beat can fire a tick twice (a worker restart, a retry); a second backup
    # in the same hour would evict a real one through retention.
    assert _due(last_backup_at=SUNDAY_3AM.replace(minute=10))[0] is False


def test_a_naive_timestamp_is_read_as_utc() -> None:
    # SQLite hands back naive datetimes; treating them as local time would skip
    # or duplicate a run depending on the server's zone.
    assert _due(last_backup_at=SUNDAY_3AM.replace(minute=10, tzinfo=None))[0] is False
