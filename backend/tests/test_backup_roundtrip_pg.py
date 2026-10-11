"""A real pg_dump/pg_restore round trip against the test database.

The unit tests inject the Postgres calls; this one proves the real ones work —
that the dump restores, and that what comes back is what went in. Skips where
the binaries or a database are absent, so it is honest about what it checked.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from app.core.config import settings
from app.services.backup.archive import extract_archive
from app.services.backup.crypto import decrypt_stream
from app.services.backup.runner import create_backup, real_pg_tools

pytestmark = pytest.mark.skipif(
    shutil.which("pg_dump") is None or shutil.which("pg_restore") is None,
    reason="Postgres client binaries not installed",
)

KEY = "a-secret-key"
URL = settings.database_url.replace("+asyncpg", "").replace("+psycopg", "")


@pytest.fixture
def engine():
    engine = sa.create_engine(URL.replace("postgresql://", "postgresql+psycopg://"))
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("DROP TABLE IF EXISTS backup_probe"))
            conn.execute(sa.text("CREATE TABLE backup_probe (note text)"))
            conn.execute(sa.text("INSERT INTO backup_probe VALUES ('before')"))
        yield engine
        with engine.begin() as conn:
            conn.execute(sa.text("DROP TABLE IF EXISTS backup_probe"))
    finally:
        engine.dispose()


def test_a_backup_restores_the_rows_it_captured(engine, tmp_path: Path) -> None:
    pg = real_pg_tools(URL)
    path = create_backup(
        backup_dir=tmp_path / "backups",
        secret_key=KEY,
        trigger="manual",
        now=datetime(2026, 10, 11, 3, 0, tzinfo=UTC),
        certs_dir=None,
        crowdsec_files={},
        pg=pg,
        app_version="0.1.0",
    )

    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE backup_probe SET note = 'after'"))

    work = tmp_path / "work"
    work.mkdir()
    with path.open("rb") as sealed, (work / "archive.tar").open("wb") as plain:
        decrypt_stream(sealed, plain, secret_key=KEY)
    with (work / "archive.tar").open("rb") as plain:
        extract_archive(plain, work)
    pg.restore(work / "db.dump")

    with engine.begin() as conn:
        assert conn.execute(sa.text("SELECT note FROM backup_probe")).scalar_one() == "before"
