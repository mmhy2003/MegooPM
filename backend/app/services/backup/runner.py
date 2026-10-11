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
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.services.backup.archive import (
    BackupArchiveError,
    BackupMeta,
    build_archive,
    extract_archive,
)
from app.services.backup.crypto import (
    BackupFormatError,
    BackupKeyError,
    decrypt_stream,
    encrypt_stream,
    key_fingerprint,
    read_header,
)
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
        # Only `public`: every MegooPM object lives there, and the restore
        # resets exactly that schema. A dump carrying a schema the restore
        # does not drop would fail on "schema already exists" — after the
        # verification step, where a failure is most expensive.
        _run(
            [
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--schema=public",
                "--file",
                str(dst),
                url,
            ]
        )

    def restore(src: Path) -> None:
        """Reset the schema and apply the dump, atomically.

        Not ``pg_restore --clean``: that drops only what the archive contains,
        so restoring a backup older than the running code would leave newer
        migrations' tables in place while ``alembic_version`` went back — and
        the catch-up migration would then fail against objects that already
        exist, with the database already replaced. Dropping the schema first
        makes the restored instance exactly the backup.

        Everything runs inside one psql transaction with ON_ERROR_STOP, so a
        failure anywhere leaves the existing database untouched.
        """
        with tempfile.TemporaryDirectory(prefix="megoopm-restore-sql-") as work:
            dump_sql = Path(work) / "dump.sql"
            _run(["pg_restore", "--no-owner", "--file", str(dump_sql), str(src)])

            script = Path(work) / "restore.sql"
            with script.open("w", encoding="utf-8") as out:
                # Drop only: `pg_dump --schema=public` emits the CREATE SCHEMA
                # itself, and a second one here would collide with it.
                out.write("DROP SCHEMA IF EXISTS public CASCADE;\n")
                with dump_sql.open("r", encoding="utf-8") as dumped:
                    shutil.copyfileobj(dumped, out)

            # Other sessions hold locks the DROP would wait on, and they are
            # about to be talking to a schema that no longer exists.
            _run(
                [
                    "psql",
                    url,
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-c",
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid()",
                ]
            )
            _run(
                [
                    "psql",
                    url,
                    "-v",
                    "ON_ERROR_STOP=1",
                    "--single-transaction",
                    "-f",
                    str(script),
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
        # Piped, not staged: writing the tar out first cost a second full copy
        # in the container's root filesystem — which the space check above does
        # not measure — and left every secret in it unencrypted for the
        # duration. The writer runs in a thread; the encryptor reads the pipe.
        read_fd, write_fd = os.pipe()
        failure: list[BaseException] = []

        def write_archive() -> None:
            try:
                with os.fdopen(write_fd, "wb") as plain:
                    build_archive(
                        plain,
                        meta=meta,
                        db_dump=dump_path,
                        certs_dir=certs_dir,
                        crowdsec_files=crowdsec_files,
                    )
            except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
                failure.append(exc)

        writer = threading.Thread(target=write_archive, name="megoopm-backup-tar")
        writer.start()
        try:
            with os.fdopen(read_fd, "rb") as plain, part.open("wb") as sealed:
                encrypt_stream(plain, sealed, secret_key=secret_key)
                sealed.flush()
                os.fsync(sealed.fileno())
        except BaseException:
            part.unlink(missing_ok=True)
            writer.join()
            raise
        writer.join()
        if failure:
            part.unlink(missing_ok=True)
            raise failure[0]

    part.replace(final)
    final.chmod(0o600)
    return final


class RestoreRejected(RuntimeError):
    """The backup was refused before anything was replaced."""


def restore_backup(
    *,
    path: Path,
    secret_key: str,
    certs_dir: Path | None,
    crowdsec_files: dict[str, Path],
    pg: PgTools,
    known_revisions: set[str],
    on_step: Callable[[str], None] = lambda step: None,
) -> BackupMeta:
    """Verify a backup completely, then replace the database and the files.

    Nothing is destroyed until the archive has been decrypted, unpacked and
    found to contain what a restore needs. ``on_step`` receives short progress
    labels; during a restore the API is 503 and they are all the UI can show.
    """
    with tempfile.TemporaryDirectory(prefix="megoopm-restore-") as work:
        work_dir = Path(work)

        on_step("verifying")
        with path.open("rb") as sealed:
            header = read_header(sealed)
        if header.fingerprint != key_fingerprint(secret_key):
            raise RestoreRejected(
                "this backup was made with a different SECRET_KEY and cannot be read"
            )

        on_step("unpacking")
        archive_path = work_dir / "archive.tar"
        try:
            with path.open("rb") as sealed, archive_path.open("wb") as plain:
                decrypt_stream(sealed, plain, secret_key=secret_key)
            with archive_path.open("rb") as plain:
                meta = extract_archive(plain, work_dir)
        except (BackupFormatError, BackupKeyError, BackupArchiveError) as exc:
            raise RestoreRejected(str(exc)) from exc

        if meta.alembic_revision and meta.alembic_revision not in known_revisions:
            raise RestoreRejected(
                f"this backup is from a newer MegooPM (database revision "
                f"{meta.alembic_revision}); upgrade before restoring it"
            )

        on_step("restoring the database")
        pg.restore(work_dir / "db.dump")

        on_step("restoring files")
        unpacked_certs = work_dir / "certs"
        if certs_dir is not None and unpacked_certs.is_dir():
            _replace_tree(unpacked_certs, certs_dir)
        unpacked_crowdsec = work_dir / "crowdsec"
        for name, target in crowdsec_files.items():
            source = unpacked_crowdsec / name
            if source.is_file():
                _replace_file(source, target)

        return meta


def _replace_tree(source: Path, target: Path) -> None:
    """Swap ``target`` for ``source`` without ever leaving it half-written."""
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(target.name + ".restoring")
    shutil.rmtree(staged, ignore_errors=True)
    shutil.copytree(source, staged)
    previous = target.with_name(target.name + ".previous")
    shutil.rmtree(previous, ignore_errors=True)
    if target.exists():
        target.rename(previous)
    staged.rename(target)
    shutil.rmtree(previous, ignore_errors=True)


def _replace_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(target.name + ".restoring")
    shutil.copy2(source, staged)
    staged.replace(target)
