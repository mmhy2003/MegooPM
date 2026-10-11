"""Taking a backup: dump, pack, seal, rename.

The Postgres calls are injected, so everything here is exercised without a
database; tests/test_backup_roundtrip_pg.py covers the real pg_dump path.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.services.backup import runner
from app.services.backup.crypto import read_header
from app.services.backup.runner import NotEnoughSpaceError, PgTools, create_backup
from app.services.backup.store import PART_SUFFIX, list_backups

KEY = "a-secret-key"
NOW = datetime(2026, 10, 11, 3, 0, tzinfo=UTC)


def _pg(dump_bytes: bytes = b"PGDMP-pretend") -> PgTools:
    def dump(dst: Path) -> None:
        dst.write_bytes(dump_bytes)

    def restore(src: Path) -> None:  # pragma: no cover - not used here
        raise AssertionError("restore must not run during a backup")

    return PgTools(dump=dump, restore=restore, revision=lambda: "0039_backups")


def _certs(tmp_path: Path) -> Path:
    certs = tmp_path / "certs" / "7"
    certs.mkdir(parents=True)
    (certs / "privkey.pem").write_text("key")
    return tmp_path / "certs"


def test_a_backup_lands_under_its_name_and_can_be_listed(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    path = create_backup(
        backup_dir=backup_dir,
        secret_key=KEY,
        trigger="manual",
        now=NOW,
        certs_dir=_certs(tmp_path),
        crowdsec_files={},
        pg=_pg(),
        app_version="0.1.0",
    )

    assert path.name == "megoopm-20261011-030000-manual.mgb"
    (row,) = list_backups(backup_dir, secret_key=KEY)
    assert row.key_matches is True
    assert row.app_version == "0.1.0"
    with path.open("rb") as handle:
        assert read_header(handle).version == 1


def test_a_failed_dump_leaves_no_backup_behind(tmp_path: Path) -> None:
    # Half a backup in the list is worse than none: someone would restore it.
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    def explode(dst: Path) -> None:
        dst.write_bytes(b"partial")
        raise RuntimeError("pg_dump: connection refused")

    with pytest.raises(RuntimeError, match="connection refused"):
        create_backup(
            backup_dir=backup_dir,
            secret_key=KEY,
            trigger="auto",
            now=NOW,
            certs_dir=None,
            crowdsec_files={},
            pg=PgTools(dump=explode, restore=lambda src: None, revision=lambda: "0039_backups"),
            app_version="0.1.0",
        )

    assert list(backup_dir.iterdir()) == []


def test_a_full_disk_is_refused_before_anything_is_written(tmp_path: Path, monkeypatch) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setattr("app.services.backup.runner.free_bytes", lambda _dir: 1024)

    with pytest.raises(NotEnoughSpaceError):
        create_backup(
            backup_dir=backup_dir,
            secret_key=KEY,
            trigger="auto",
            now=NOW,
            certs_dir=_certs(tmp_path),
            crowdsec_files={},
            pg=_pg(b"x" * 100_000),
            app_version="0.1.0",
        )

    assert not any(p.name.endswith(PART_SUFFIX) for p in backup_dir.iterdir())


def test_restoring_resets_the_schema_in_one_transaction(monkeypatch) -> None:
    """`pg_restore --clean` only drops what the archive contains.

    Restoring a backup older than the running code would leave tables and
    columns from newer migrations in place, and bring `alembic_version` back to
    the old revision — so `alembic upgrade head` then re-runs those migrations
    against objects that already exist and fails, with the database already
    replaced. The restore therefore resets the schema and applies the dump as
    one transaction: either the instance is the backup, or it is untouched.
    """
    from app.services.backup.runner import real_pg_tools

    calls: list[list[str]] = []
    scripts: list[str] = []

    def fake_run(argv, capture_output=True, text=True, env=None, check=False):
        calls.append(argv)
        if "-f" in argv and argv[0] == "psql":
            # Read it here: the temp directory is gone once restore() returns.
            scripts.append(Path(argv[argv.index("-f") + 1]).read_text(encoding="utf-8"))
        if argv[0] == "pg_restore":
            # pg_restore --file <path> writes the dump out as SQL.
            Path(argv[argv.index("--file") + 1]).write_text("-- dump sql\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("app.services.backup.runner.subprocess.run", fake_run)
    real_pg_tools("postgresql+asyncpg://u:p@h/db").restore(Path("/tmp/db.dump"))

    programs = [argv[0] for argv in calls]
    assert "psql" in programs

    terminate = next(a for a in calls if any("pg_terminate_backend" in part for part in a))
    assert terminate[0] == "psql"

    apply_sql = next(a for a in calls if a[0] == "psql" and "--single-transaction" in a)
    assert "ON_ERROR_STOP=1" in apply_sql
    assert "-f" in apply_sql
    (script,) = scripts
    assert "DROP SCHEMA" in script
    assert "-- dump sql" in script
    # The dump recreates the schema; a second CREATE here would collide with it.
    assert "CREATE SCHEMA" not in script.split("-- dump sql")[0]
    # The URL handed to psql is a libpq one; the SQLAlchemy driver suffix is not.
    assert all("+asyncpg" not in part for argv in calls for part in argv)


def test_the_archive_is_never_written_to_disk_in_the_clear(tmp_path: Path, monkeypatch) -> None:
    """The tar is piped into the encryptor, not staged beside the dump.

    Staging it cost a second full copy in the container's /tmp — the root
    filesystem, which `free_bytes(backup_dir)` does not measure — and left
    every secret and private key sitting unencrypted there for the duration.
    """
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    seen: list[set[str]] = []
    real_build = runner.build_archive

    def spy(dst, **kwargs):
        # Whatever else is in the work directory while the tar is being built,
        # it must not be the tar itself.
        work = Path(kwargs["db_dump"]).parent
        seen.append({p.name for p in work.iterdir()})
        return real_build(dst, **kwargs)

    monkeypatch.setattr(runner, "build_archive", spy)
    create_backup(
        backup_dir=backup_dir,
        secret_key=KEY,
        trigger="manual",
        now=NOW,
        certs_dir=_certs(tmp_path),
        crowdsec_files={},
        pg=_pg(),
        app_version="0.1.0",
    )

    assert seen, "build_archive was never called"
    assert all(names == {"db.dump"} for names in seen), seen
