"""Restoring: everything is verified before anything is replaced.

The ordering is the design. A restore that drops the database and then finds
the archive unreadable has destroyed the instance it was asked to protect, so
every check that can run first, runs first.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.services.backup.archive import BackupMeta, build_archive
from app.services.backup.crypto import encrypt_stream
from app.services.backup.runner import PgTools, RestoreRejected, restore_backup

KEY = "a-secret-key"
REVISIONS = {"0039_backups"}


def _sealed_backup(
    tmp_path: Path, *, secret_key: str = KEY, revision: str = "0039_backups"
) -> Path:
    dump = tmp_path / "db.dump"
    dump.write_bytes(b"PGDMP-pretend")
    certs = tmp_path / "src-certs" / "7"
    certs.mkdir(parents=True)
    (certs / "privkey.pem").write_text("restored-key")
    meta = BackupMeta(
        created_at=datetime(2026, 10, 11, 3, 0, tzinfo=UTC),
        app_version="0.1.0",
        alembic_revision=revision,
        trigger="manual",
        db_bytes=13,
        cert_bytes=12,
    )
    plain = io.BytesIO()
    build_archive(
        plain, meta=meta, db_dump=dump, certs_dir=tmp_path / "src-certs", crowdsec_files={}
    )
    plain.seek(0)
    path = tmp_path / "megoopm-20261011-030000-manual.mgb"
    with path.open("wb") as out:
        encrypt_stream(plain, out, secret_key=secret_key)
    return path


def _pg(restored: list[Path]) -> PgTools:
    return PgTools(
        dump=lambda dst: (_ for _ in ()).throw(AssertionError("no dump during a restore")),
        restore=restored.append,
        revision=lambda: "0039_backups",
    )


def test_a_restore_replaces_the_database_and_the_certificates(tmp_path: Path) -> None:
    restored: list[Path] = []
    certs_dir = tmp_path / "live-certs"
    (certs_dir / "7").mkdir(parents=True)
    (certs_dir / "7" / "privkey.pem").write_text("old-key")
    steps: list[str] = []

    meta = restore_backup(
        path=_sealed_backup(tmp_path),
        secret_key=KEY,
        certs_dir=certs_dir,
        crowdsec_files={},
        pg=_pg(restored),
        known_revisions=REVISIONS,
        on_step=steps.append,
    )

    assert meta.app_version == "0.1.0"
    assert len(restored) == 1
    assert (certs_dir / "7" / "privkey.pem").read_text() == "restored-key"
    assert steps  # the UI shows these while the API is otherwise 503


def test_a_backup_from_another_instance_is_refused(tmp_path: Path) -> None:
    restored: list[Path] = []
    with pytest.raises(RestoreRejected, match="SECRET_KEY"):
        restore_backup(
            path=_sealed_backup(tmp_path, secret_key="another-key"),
            secret_key=KEY,
            certs_dir=None,
            crowdsec_files={},
            pg=_pg(restored),
            known_revisions=REVISIONS,
        )
    assert restored == []


def test_a_backup_from_a_newer_megoopm_is_refused(tmp_path: Path) -> None:
    # Its schema is ahead of this code; restoring it would leave an instance
    # the running version cannot read.
    restored: list[Path] = []
    with pytest.raises(RestoreRejected, match="newer"):
        restore_backup(
            path=_sealed_backup(tmp_path, revision="0099_from_the_future"),
            secret_key=KEY,
            certs_dir=None,
            crowdsec_files={},
            pg=_pg(restored),
            known_revisions=REVISIONS,
        )
    assert restored == []


def test_a_file_that_is_not_a_backup_never_reaches_the_database(tmp_path: Path) -> None:
    # Review Focus 1: a valid envelope around garbage must be caught before
    # pg_restore --clean drops a single table.
    restored: list[Path] = []
    junk = tmp_path / "megoopm-20261011-030000-upload.mgb"
    with junk.open("wb") as out:
        encrypt_stream(io.BytesIO(b"not a tar at all"), out, secret_key=KEY)

    with pytest.raises(RestoreRejected):
        restore_backup(
            path=junk,
            secret_key=KEY,
            certs_dir=None,
            crowdsec_files={},
            pg=_pg(restored),
            known_revisions=REVISIONS,
        )

    assert restored == []


def test_a_failing_pg_restore_leaves_the_certificates_alone(tmp_path: Path) -> None:
    # Files are replaced only after the database is back: a restore that failed
    # halfway must not leave new keys beside an old database.
    certs_dir = tmp_path / "live-certs"
    (certs_dir / "7").mkdir(parents=True)
    (certs_dir / "7" / "privkey.pem").write_text("old-key")

    def explode(src: Path) -> None:
        raise RuntimeError("pg_restore: relation does not exist")

    with pytest.raises(RuntimeError, match="pg_restore"):
        restore_backup(
            path=_sealed_backup(tmp_path),
            secret_key=KEY,
            certs_dir=certs_dir,
            crowdsec_files={},
            pg=PgTools(dump=lambda dst: None, restore=explode, revision=lambda: "0039_backups"),
            known_revisions=REVISIONS,
        )

    assert (certs_dir / "7" / "privkey.pem").read_text() == "old-key"
