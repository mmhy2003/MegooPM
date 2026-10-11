"""Taking a backup, and the Postgres calls it needs.

The dump/restore calls are injected (:class:`PgTools`) so the orchestration is
testable without a database, and so a future deployment can swap how it reaches
Postgres without touching this file.

The sequence is: check space, dump, pack, seal into ``<name>.part``, fsync,
rename. A failure anywhere before the rename leaves the directory as it was —
a half-written backup that someone later restores is the worst outcome here.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.services.backup.archive import BackupMeta, build_archive
from app.services.backup.crypto import encrypt_stream
from app.services.backup.store import PART_SUFFIX, free_bytes, new_name

#: Head-room over the estimated size before a backup is attempted.
_SPACE_MARGIN = 1.2


class NotEnoughSpaceError(RuntimeError):
    """The backup directory does not have room for this backup."""


@dataclass(frozen=True, slots=True)
class PgTools:
    #: Write a custom-format dump of the whole database to this path.
    dump: Callable[[Path], None]
    #: Replace the database from this dump path.
    restore: Callable[[Path], None]
    #: The alembic revision the database is currently at.
    revision: Callable[[], str]


def _run(argv: list[str], *, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(argv, capture_output=True, text=True, env=env, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{argv[0]} failed: {result.stderr.strip() or result.returncode}")


def real_pg_tools(database_url: str) -> PgTools:
    """pg_dump/pg_restore/psql against ``database_url`` (a libpq URL)."""
    url = database_url.replace("+asyncpg", "").replace("+psycopg", "")

    def dump(dst: Path) -> None:
        _run(["pg_dump", "--format=custom", "--no-owner", "--file", str(dst), url])

    def restore(src: Path) -> None:
        _run(
            [
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--single-transaction",
                "--dbname",
                url,
                str(src),
            ]
        )

    def revision() -> str:
        result = subprocess.run(
            ["psql", url, "-tAc", "SELECT version_num FROM alembic_version"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    return PgTools(dump=dump, restore=restore, revision=revision)


def _tree_bytes(root: Path | None) -> int:
    if root is None or not root.is_dir():
        return 0
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def create_backup(
    *,
    backup_dir: Path,
    secret_key: str,
    trigger: str,
    now: datetime,
    certs_dir: Path | None,
    crowdsec_files: dict[str, Path],
    pg: PgTools,
    app_version: str,
) -> Path:
    """Dump, pack, seal and rename. Returns the finished file's path."""
    # 0700: a backup holds TLS private keys and every stored secret.
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    final = backup_dir / new_name(now, trigger)
    part = final.with_name(final.name + PART_SUFFIX)

    with tempfile.TemporaryDirectory(prefix="megoopm-backup-") as work:
        dump_path = Path(work) / "db.dump"
        pg.dump(dump_path)

        cert_bytes = _tree_bytes(certs_dir)
        needed = int((dump_path.stat().st_size + cert_bytes) * _SPACE_MARGIN)
        if free_bytes(backup_dir) < needed:
            raise NotEnoughSpaceError(
                f"{needed // 1024 // 1024} MB needed in {backup_dir}, "
                f"{free_bytes(backup_dir) // 1024 // 1024} MB free"
            )

        meta = BackupMeta(
            created_at=now,
            app_version=app_version,
            alembic_revision=pg.revision(),
            trigger=trigger,
            db_bytes=dump_path.stat().st_size,
            cert_bytes=cert_bytes,
        )
        plain_path = Path(work) / "archive.tar"
        with plain_path.open("wb") as plain:
            build_archive(
                plain,
                meta=meta,
                db_dump=dump_path,
                certs_dir=certs_dir,
                crowdsec_files=crowdsec_files,
            )

        try:
            with plain_path.open("rb") as plain, part.open("wb") as sealed:
                encrypt_stream(plain, sealed, secret_key=secret_key)
                sealed.flush()
                os.fsync(sealed.fileno())
        except BaseException:
            part.unlink(missing_ok=True)
            raise

    part.replace(final)
    final.chmod(0o600)
    return final
