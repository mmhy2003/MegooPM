"""Whether the hourly tick should take a backup.

Mirrors services/crowdsec/hub.is_due: beat ticks every hour and the decision
lives here, so a tick that could not run on time is skipped rather than queued
and run at the wrong hour.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.enums import BackupFrequency


def is_backup_due(
    *,
    now: datetime,
    frequency: BackupFrequency,
    weekday: int,
    hour_utc: int,
    last_backup_at: datetime | None,
) -> tuple[bool, str]:
    """Whether the tick at ``now`` should take a backup, and why not."""
    if frequency is BackupFrequency.off:
        return False, "automatic backups are off"
    if now.hour != hour_utc:
        return False, "not the configured hour"
    if frequency is BackupFrequency.weekly and now.weekday() != weekday:
        return False, "not the configured weekday"
    if last_backup_at is not None:
        # SQLite (the test factory) hands back naive datetimes; treat as UTC.
        if last_backup_at.tzinfo is None:
            last_backup_at = last_backup_at.replace(tzinfo=UTC)
        hour = lambda moment: moment.replace(minute=0, second=0, microsecond=0)  # noqa: E731
        if hour(last_backup_at) == hour(now):
            return False, "already ran this hour"
    return True, "due"
