"""The tar inside the envelope: meta first, then the dump, certs and CrowdSec.

meta.json is written first on purpose — the backup list reads it out of the
first decrypted chunk, without unpacking gigabytes.
"""

from __future__ import annotations

import io
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.services.backup.archive import (
    MEMBER_DB,
    MEMBER_META,
    BackupArchiveError,
    BackupMeta,
    build_archive,
    extract_archive,
    meta_from_first_chunk,
)

META = BackupMeta(
    created_at=datetime(2026, 10, 11, 3, 0, tzinfo=UTC),
    app_version="0.1.0",
    alembic_revision="0039_backups",
    trigger="auto",
    db_bytes=12,
    cert_bytes=3,
)


def _source(tmp_path: Path) -> tuple[Path, Path, dict[str, Path]]:
    dump = tmp_path / "db.dump"
    dump.write_bytes(b"PGDMP-pretend")
    certs = tmp_path / "certs"
    (certs / "7").mkdir(parents=True)
    (certs / "7" / "privkey.pem").write_text("key")
    (certs / "7" / "privkey.pem").chmod(0o600)
    whitelist = tmp_path / "whitelists.yaml"
    whitelist.write_text("# none")
    return dump, certs, {"whitelists.yaml": whitelist}


def _built(tmp_path: Path) -> io.BytesIO:
    dump, certs, crowdsec = _source(tmp_path)
    out = io.BytesIO()
    build_archive(out, meta=META, db_dump=dump, certs_dir=certs, crowdsec_files=crowdsec)
    out.seek(0)
    return out


def test_every_part_of_the_instance_is_in_the_archive(tmp_path: Path) -> None:
    with tarfile.open(fileobj=_built(tmp_path), mode="r:") as tar:
        names = tar.getnames()
    assert names[0] == MEMBER_META  # first, so the list can read it cheaply
    assert MEMBER_DB in names
    assert "certs/7/privkey.pem" in names
    assert "crowdsec/whitelists.yaml" in names


def test_unpacking_restores_the_files_and_their_modes(tmp_path: Path) -> None:
    # A private key that comes back world-readable is a quiet security defect.
    dest = tmp_path / "out"
    meta = extract_archive(_built(tmp_path), dest)
    assert meta == META
    assert (dest / "certs" / "7" / "privkey.pem").read_text() == "key"
    assert (dest / "certs" / "7" / "privkey.pem").stat().st_mode & 0o777 == 0o600
    assert (dest / MEMBER_DB).read_bytes() == b"PGDMP-pretend"


def test_the_list_reads_meta_from_the_first_chunk(tmp_path: Path) -> None:
    assert meta_from_first_chunk(_built(tmp_path).read(1024 * 1024)) == META


def test_an_archive_without_a_dump_is_refused(tmp_path: Path) -> None:
    # Caught before a restore drops anything: a tar missing db.dump would
    # otherwise empty the database and have nothing to put back.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        raw = META.to_json()
        info = tarfile.TarInfo(MEMBER_META)
        info.size = len(raw)
        tar.addfile(info, io.BytesIO(raw))
    buf.seek(0)
    with pytest.raises(BackupArchiveError):
        extract_archive(buf, tmp_path / "out")


def test_a_member_pointing_outside_the_destination_is_refused(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name in (MEMBER_META, MEMBER_DB, "../escaped"):
            raw = META.to_json() if name == MEMBER_META else b"x"
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
    buf.seek(0)
    with pytest.raises(BackupArchiveError):
        extract_archive(buf, tmp_path / "out")


def test_garbage_is_refused_rather_than_unpacked(tmp_path: Path) -> None:
    with pytest.raises(BackupArchiveError):
        extract_archive(io.BytesIO(b"this is not a tar"), tmp_path / "out")
