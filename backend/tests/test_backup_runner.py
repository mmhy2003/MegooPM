"""Taking a backup: dump, pack, seal, rename.

The Postgres calls are injected, so everything here is exercised without a
database; tests/test_backup_roundtrip_pg.py covers the real pg_dump path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
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
