"""What the backups API returns. Mirrors services/backup/store.BackupFile."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.services.backup.store import BackupFile


class BackupRead(BaseModel):
    """One file in the backup directory, as the page shows it."""

    name: str
    size_bytes: int
    created_at: datetime
    trigger: str = Field(description="manual | auto | upload")
    #: False when the file was made under a different SECRET_KEY: it is listed
    #: (so it can be deleted) but cannot be read or restored here.
    key_matches: bool
    app_version: str | None = None
    alembic_revision: str | None = None

    @classmethod
    def from_file(cls, row: BackupFile) -> BackupRead:
        return cls(
            name=row.name,
            size_bytes=row.size_bytes,
            created_at=row.created_at,
            trigger=row.trigger,
            key_matches=row.key_matches,
            app_version=row.app_version,
            alembic_revision=row.alembic_revision,
        )


class BackupList(BaseModel):
    """The directory's contents, newest first, plus room left on the disk."""

    # Required, not defaulted: an optional list in the schema becomes
    # `items?: …` in the generated client, and every caller then has to
    # handle an absence the API never produces.
    items: list[BackupRead]
    free_bytes: int


class RestoreStatus(BaseModel):
    """What a restore is doing — the one thing the API answers during one."""

    running: bool
    step: str | None = None
    started_at: str | None = None
    #: Why the last restore stopped, kept briefly after it ends.
    error: str | None = None
