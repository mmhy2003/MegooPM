"""The backup directory, treated as the source of truth.

No table of backups: the page lists what is on disk, so a file copied in by
hand appears and a file deleted on the host disappears. Everything the list
shows therefore comes out of the file — its name (time and trigger), its stat
(size), and, when the key matches, the meta.json in its first chunk.

Writes land on `<name>.part` and are renamed, so a backup interrupted by a full
disk is never offered for restore.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.services.backup.archive import meta_from_first_chunk
from app.services.backup.crypto import (
    BackupFormatError,
    BackupKeyError,
    decrypt_first_chunk,
    key_fingerprint,
    read_header,
)

TRIGGERS = ("manual", "auto", "upload")
NAME_RE = re.compile(r"^megoopm-\d{8}-\d{6}-(manual|auto|upload)\.mgb$")
PART_SUFFIX = ".part"


class UnknownBackupError(RuntimeError):
    """No backup by that name (or the name is not one we would ever write)."""


@dataclass(frozen=True, slots=True)
class BackupFile:
    name: str
    size_bytes: int
    created_at: datetime
    trigger: str
    key_matches: bool
    app_version: str | None
    alembic_revision: str | None


def new_name(now: datetime, trigger: str) -> str:
    if trigger not in TRIGGERS:
        raise ValueError(f"unknown trigger {trigger!r}")
    return f"megoopm-{now.astimezone(UTC):%Y%m%d-%H%M%S}-{trigger}.mgb"


def safe_path(backup_dir: Path, name: str) -> Path:
    """Resolve a name from a request, or refuse it. Never escapes the directory."""
    if not NAME_RE.match(name):
        raise UnknownBackupError(f"no backup named {name!r}")
    path = backup_dir / name
    if not path.is_file():
        raise UnknownBackupError(f"no backup named {name!r}")
    return path


def _created_at(name: str) -> datetime:
    return datetime.strptime(name[8:23], "%Y%m%d-%H%M%S").replace(tzinfo=UTC)


def describe(path: Path, *, secret_key: str) -> BackupFile:
    name = path.name
    trigger = name.rsplit("-", 1)[1].removesuffix(".mgb")
    key_matches = False
    app_version: str | None = None
    revision: str | None = None
    try:
        with path.open("rb") as handle:
            header = read_header(handle)
            key_matches = header.fingerprint == key_fingerprint(secret_key)
            if key_matches:
                handle.seek(0)
                meta = meta_from_first_chunk(decrypt_first_chunk(handle, secret_key=secret_key))
                if meta is not None:
                    app_version = meta.app_version
                    revision = meta.alembic_revision
    except (OSError, BackupFormatError, BackupKeyError):
        # A damaged file still belongs in the list: an admin has to see it to
        # delete it. It simply offers nothing beyond its name and size.
        pass
    return BackupFile(
        name=name,
        size_bytes=path.stat().st_size,
        created_at=_created_at(name),
        trigger=trigger,
        key_matches=key_matches,
        app_version=app_version,
        alembic_revision=revision,
    )


def list_backups(backup_dir: Path, *, secret_key: str) -> list[BackupFile]:
    if not backup_dir.is_dir():
        return []
    rows = [
        describe(path, secret_key=secret_key)
        for path in backup_dir.iterdir()
        if path.is_file() and NAME_RE.match(path.name)
    ]
    return sorted(rows, key=lambda row: row.name, reverse=True)


def apply_retention(backup_dir: Path, *, keep: int) -> list[str]:
    """Delete all but the newest ``keep`` automatic backups. Returns their names."""
    automatic = sorted(
        (p for p in backup_dir.glob("*-auto.mgb") if NAME_RE.match(p.name)),
        key=lambda p: p.name,
        reverse=True,
    )
    deleted: list[str] = []
    for path in automatic[max(keep, 0) :]:
        path.unlink(missing_ok=True)
        deleted.append(path.name)
    return deleted


def free_bytes(backup_dir: Path) -> int:
    return shutil.disk_usage(backup_dir).free
