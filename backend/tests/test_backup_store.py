"""The backup directory is the list: no table, no drift.

A file an operator copies in appears; one they delete disappears. Which means
every fact the UI shows has to come from the file itself — its name, its stat,
and (when the key matches) the meta.json in its first chunk.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.services.backup.archive import BackupMeta, build_archive
from app.services.backup.crypto import encrypt_stream
from app.services.backup.store import (
    PART_SUFFIX,
    UnknownBackupError,
    apply_retention,
    free_bytes,
    list_backups,
    new_name,
    safe_path,
    sweep_interrupted,
)

KEY = "a-secret-key"


def _write_backup(backup_dir: Path, name: str, *, secret_key: str = KEY) -> Path:
    dump = backup_dir / "tmp.dump"
    dump.write_bytes(b"PGDMP-pretend")
    meta = BackupMeta(
        created_at=datetime(2026, 10, 11, 3, 0, tzinfo=UTC),
        app_version="0.1.0",
        alembic_revision="0039_backups",
        trigger=name.rsplit("-", 1)[1].removesuffix(".mgb"),
        db_bytes=13,
        cert_bytes=0,
    )
    plain = io.BytesIO()
    build_archive(plain, meta=meta, db_dump=dump, certs_dir=None, crowdsec_files={})
    plain.seek(0)
    dump.unlink()
    path = backup_dir / name
    with path.open("wb") as out:
        encrypt_stream(plain, out, secret_key=secret_key)
    return path


def test_a_name_carries_the_time_and_how_it_was_made() -> None:
    assert new_name(datetime(2026, 10, 11, 3, 0, 5, tzinfo=UTC), "auto") == (
        "megoopm-20261011-030005-auto.mgb"
    )


@pytest.mark.parametrize(
    "name",
    ["../../etc/passwd", "megoopm-20261011-030005-auto.mgb/../x", "notes.txt", "megoopm-bad.mgb"],
)
def test_a_name_that_is_not_ours_is_refused(tmp_path: Path, name: str) -> None:
    # Every route takes a name from the URL; this is what keeps it in the box.
    with pytest.raises(UnknownBackupError):
        safe_path(tmp_path, name)


def test_listing_reads_each_file_and_puts_the_newest_first(tmp_path: Path) -> None:
    _write_backup(tmp_path, "megoopm-20261010-030000-auto.mgb")
    _write_backup(tmp_path, "megoopm-20261011-030000-manual.mgb")

    rows = list_backups(tmp_path, secret_key=KEY)

    assert [r.name for r in rows] == [
        "megoopm-20261011-030000-manual.mgb",
        "megoopm-20261010-030000-auto.mgb",
    ]
    assert rows[0].trigger == "manual"
    assert rows[0].key_matches is True
    assert rows[0].app_version == "0.1.0"
    assert rows[0].size_bytes > 0


def test_a_backup_from_another_instance_is_listed_but_marked(tmp_path: Path) -> None:
    # Visible, because an admin has to be able to see and delete it; marked,
    # because restoring it would fail and its secrets would be unreadable.
    _write_backup(tmp_path, "megoopm-20261011-030000-upload.mgb", secret_key="another-key")

    (row,) = list_backups(tmp_path, secret_key=KEY)

    assert row.key_matches is False
    assert row.app_version is None


def test_a_half_written_backup_is_not_offered(tmp_path: Path) -> None:
    # A disk that filled mid-backup leaves a .part file. Restoring it would
    # restore a truncated database.
    _write_backup(tmp_path, f"megoopm-20261011-030000-auto.mgb{PART_SUFFIX}")
    (tmp_path / "readme.txt").write_text("not a backup")

    assert list_backups(tmp_path, secret_key=KEY) == []


def test_retention_keeps_the_newest_automatic_backups(tmp_path: Path) -> None:
    for day in (9, 10, 11):
        _write_backup(tmp_path, f"megoopm-202610{day:02d}-030000-auto.mgb")

    deleted = apply_retention(tmp_path, keep=2)

    assert deleted == ["megoopm-20261009-030000-auto.mgb"]
    assert not (tmp_path / "megoopm-20261009-030000-auto.mgb").exists()


def test_retention_never_touches_a_manual_backup_or_an_upload(tmp_path: Path) -> None:
    # The copy an admin took before a risky change must not expire overnight.
    _write_backup(tmp_path, "megoopm-20261001-030000-manual.mgb")
    _write_backup(tmp_path, "megoopm-20261002-030000-upload.mgb")
    _write_backup(tmp_path, "megoopm-20261011-030000-auto.mgb")

    assert apply_retention(tmp_path, keep=1) == []
    assert len(list(tmp_path.glob("*.mgb"))) == 3


def test_free_space_is_reported_for_the_backup_directory(tmp_path: Path) -> None:
    assert free_bytes(tmp_path) > 0


def test_a_name_with_an_impossible_date_is_refused_rather_than_crashing(tmp_path: Path) -> None:
    # The regex allows any 8 digits. Parsed as a date, "00000000" raises — and
    # an unhandled raise in the listing takes the whole page down, with the
    # offending file un-deletable because every route refuses it too.
    _write_backup(tmp_path, "megoopm-20261011-030000-manual.mgb")
    (tmp_path / "megoopm-00000000-000000-manual.mgb").write_bytes(b"not really a backup")

    rows = list_backups(tmp_path, secret_key=KEY)

    assert [r.name for r in rows] == [
        "megoopm-20261011-030000-manual.mgb",
        "megoopm-00000000-000000-manual.mgb",
    ]


def test_a_file_that_vanishes_mid_listing_does_not_break_the_page(
    tmp_path: Path, monkeypatch
) -> None:
    # Retention or an operator can delete a file between the scan and the stat.
    _write_backup(tmp_path, "megoopm-20261011-030000-auto.mgb")
    real_stat = Path.stat

    def vanishing(self, *args, **kwargs):
        if self.name.endswith(".mgb"):
            raise FileNotFoundError(self)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", vanishing)

    assert list_backups(tmp_path, secret_key=KEY) == []


def test_interrupted_backups_are_swept_so_the_disk_is_not_eaten(tmp_path: Path) -> None:
    # A worker killed mid-backup leaves a multi-GB .part the page cannot show
    # and no route can delete; a few of those and backups start failing for
    # want of space with nothing on screen to explain it.
    import os
    import time

    stale = tmp_path / f"megoopm-20261001-030000-auto.mgb{PART_SUFFIX}"
    stale.write_bytes(b"half a backup")
    os.utime(stale, (time.time() - 48 * 3600, time.time() - 48 * 3600))
    fresh = tmp_path / f"megoopm-20261011-030000-auto.mgb{PART_SUFFIX}"
    fresh.write_bytes(b"a backup being written right now")

    swept = sweep_interrupted(tmp_path)

    assert swept == [stale.name]
    assert not stale.exists()
    assert fresh.exists(), "a backup in flight must not be swept out from under itself"
