"""The backup routes: admin-only, name-safe, and never a surprise restore.

Uses a tmp backup directory through the settings override the other API tests
use for paths, so nothing here touches a real backup.
"""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import app.api.routes._config_writes as config_writes
import pytest
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.user import UserRole
from app.schemas.tasks import TaskEnqueued
from app.services import user as user_service
from app.services.backup.archive import BackupMeta, build_archive
from app.services.backup.crypto import encrypt_stream
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


def _sealed_bytes(trigger: str = "manual") -> bytes:
    """A real sealed archive: the routes validate what they are handed."""
    meta = BackupMeta(
        created_at=datetime(2026, 10, 11, 3, 0, tzinfo=UTC),
        app_version="0.1.0",
        alembic_revision="0039_backups",
        trigger=trigger,
        db_bytes=13,
        cert_bytes=0,
    )
    import tempfile

    with tempfile.TemporaryDirectory() as work:
        dump = Path(work) / "db.dump"
        dump.write_bytes(b"PGDMP-pretend")
        plain = io.BytesIO()
        build_archive(plain, meta=meta, db_dump=dump, certs_dir=None, crowdsec_files={})
    plain.seek(0)
    sealed = io.BytesIO()
    encrypt_stream(plain, sealed, secret_key=settings.secret_key)
    return sealed.getvalue()


def _write_backup(backup_dir: Path, name: str) -> Path:
    path = backup_dir / name
    path.write_bytes(_sealed_bytes(name.rsplit("-", 1)[1].removesuffix(".mgb")))
    return path


@pytest.fixture
def backup_dir(tmp_path: Path, monkeypatch) -> Path:
    directory = tmp_path / "backups"
    directory.mkdir()
    monkeypatch.setattr(settings, "backup_dir", str(directory))
    return directory


@pytest.fixture
async def pg_conn() -> AsyncIterator:
    """A single Postgres connection in one rolled-back transaction (or skip)."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        conn = await engine.connect()
    except Exception:  # pragma: no cover - environment without a database
        await engine.dispose()
        pytest.skip("No database reachable at DATABASE_URL")

    trans = await conn.begin()
    await conn.run_sync(Base.metadata.create_all)
    try:
        yield conn
    finally:
        if trans.is_active:
            await trans.rollback()
        await conn.close()
        await engine.dispose()


@pytest.fixture(autouse=True)
async def seeded_settings(pg_conn) -> None:
    """`create_all` builds the table but does not run the migration's seed."""
    await pg_conn.execute(
        text("INSERT INTO instance_settings (id, default_site_mode) VALUES (1, 'not_found')")
    )


@pytest.fixture(autouse=True)
def enqueued_tasks(monkeypatch):
    """Celery is not running here: record what the routes enqueue."""
    calls: dict[str, list] = {"create": [], "restore": []}

    class _Result:
        id = "test-task"
        status = "PENDING"

    import app.api.routes.backups as routes

    monkeypatch.setattr(
        routes.create_backup_task,
        "delay",
        lambda *a, **k: (calls["create"].append(a), _Result())[1],
    )
    monkeypatch.setattr(
        routes.restore_backup_task,
        "delay",
        lambda *a, **k: (calls["restore"].append(a), _Result())[1],
    )
    return calls


@pytest.fixture
async def client(pg_conn, monkeypatch) -> AsyncIterator[AsyncClient]:
    factory = async_sessionmaker(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )

    async def _override_get_session() -> AsyncIterator:
        async with factory() as session:
            yield session

    monkeypatch.setattr(
        config_writes,
        "enqueue_nginx_reload",
        lambda: TaskEnqueued(task_id="test-reload-task", status="PENDING"),
    )

    app.dependency_overrides[get_session] = _override_get_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_session, None)


async def _token(pg_conn, client: AsyncClient, email: str, role: UserRole) -> str:
    factory = async_sessionmaker(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    async with factory() as session:
        await user_service.create_user(session, email=email, password="adminpass123", role=role)
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "adminpass123"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture
async def auth(pg_conn, client: AsyncClient) -> dict[str, str]:
    token = await _token(pg_conn, client, "backup-admin@example.com", UserRole.admin)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def member_auth(pg_conn, client: AsyncClient) -> dict[str, str]:
    token = await _token(pg_conn, client, "backup-member@example.com", UserRole.member)
    return {"Authorization": f"Bearer {token}"}


async def test_the_list_shows_what_is_in_the_directory(client, auth, backup_dir) -> None:
    _write_backup(backup_dir, "megoopm-20261011-030000-manual.mgb")

    body = (await client.get("/api/v1/backups", headers=auth)).json()

    assert [row["name"] for row in body["items"]] == ["megoopm-20261011-030000-manual.mgb"]
    assert body["items"][0]["key_matches"] is True
    assert body["free_bytes"] > 0


async def test_a_member_cannot_see_or_take_backups(client, member_auth) -> None:
    assert (await client.get("/api/v1/backups", headers=member_auth)).status_code == 403
    assert (await client.post("/api/v1/backups", headers=member_auth)).status_code == 403


async def test_taking_a_backup_enqueues_the_task(client, auth, backup_dir) -> None:
    response = await client.post("/api/v1/backups", headers=auth)

    assert response.status_code == 202
    assert response.json()["task_id"]


async def test_downloading_streams_the_file(client, auth, backup_dir) -> None:
    path = _write_backup(backup_dir, "megoopm-20261011-030000-manual.mgb")

    response = await client.get(
        "/api/v1/backups/megoopm-20261011-030000-manual.mgb/download", headers=auth
    )

    assert response.status_code == 200
    assert response.content == path.read_bytes()


@pytest.mark.parametrize(
    "name", ["../../etc/passwd", "secrets.txt", "megoopm-20261011-030000-manual.mgb.part"]
)
async def test_a_name_that_is_not_a_backup_is_a_404(client, auth, backup_dir, name) -> None:
    # Review Focus 3: the name comes from the URL, so this is the boundary.
    assert (await client.get(f"/api/v1/backups/{name}/download", headers=auth)).status_code == 404
    assert (await client.delete(f"/api/v1/backups/{name}", headers=auth)).status_code == 404


async def test_deleting_removes_the_file(client, auth, backup_dir) -> None:
    _write_backup(backup_dir, "megoopm-20261011-030000-auto.mgb")

    response = await client.delete("/api/v1/backups/megoopm-20261011-030000-auto.mgb", headers=auth)

    assert response.status_code == 204
    assert list(backup_dir.glob("*.mgb")) == []


async def test_an_upload_lands_as_an_upload(client, auth, backup_dir) -> None:
    # Renamed on arrival: a file called ...-auto.mgb from somewhere else must
    # not be swept by this instance's retention.
    payload = _sealed_bytes()

    response = await client.post(
        "/api/v1/backups/upload",
        headers=auth,
        files={
            "file": (
                "megoopm-20261011-030000-auto.mgb",
                io.BytesIO(payload),
                "application/octet-stream",
            )
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["trigger"] == "upload"
    assert [p.name.rsplit("-", 1)[1] for p in backup_dir.glob("*.mgb")] == ["upload.mgb"]


async def test_an_upload_that_is_not_a_backup_is_refused(client, auth, backup_dir) -> None:
    response = await client.post(
        "/api/v1/backups/upload",
        headers=auth,
        files={
            "file": (
                "megoopm-20261011-030000-upload.mgb",
                io.BytesIO(b"nope"),
                "application/octet-stream",
            )
        },
    )

    assert response.status_code == 400
    assert list(backup_dir.iterdir()) == []


async def test_an_oversized_upload_is_refused_and_leaves_nothing(
    client, auth, backup_dir, monkeypatch
) -> None:
    # Review Focus 3: the cap must bite while streaming, not after the disk is
    # full. settings.backup_max_bytes is tiny here.
    monkeypatch.setattr("app.api.routes.backups.settings.backup_max_bytes", 16)

    response = await client.post(
        "/api/v1/backups/upload",
        headers=auth,
        files={
            "file": (
                "megoopm-20261011-030000-upload.mgb",
                io.BytesIO(_sealed_bytes()),
                "application/octet-stream",
            )
        },
    )

    assert response.status_code == 413
    assert list(backup_dir.iterdir()) == []


async def test_restore_status_is_quiet_when_nothing_runs(client, auth) -> None:
    body = (await client.get("/api/v1/backups/restore-status", headers=auth)).json()

    assert body["running"] is False


async def test_restoring_enqueues_the_task_and_audits_it(client, auth, backup_dir) -> None:
    _write_backup(backup_dir, "megoopm-20261011-030000-manual.mgb")

    response = await client.post(
        "/api/v1/backups/megoopm-20261011-030000-manual.mgb/restore", headers=auth
    )

    assert response.status_code == 202
    audit = await client.get("/api/v1/audit-log?object_type=backup", headers=auth)
    assert audit.json()["items"][0]["action"] == "restore"


async def test_an_upload_is_refused_before_its_body_is_read(
    client, auth, backup_dir, monkeypatch
) -> None:
    """The cap has to bite on Content-Length, not after the body is spooled.

    FastAPI parses the whole multipart body before the endpoint runs, into an
    unbounded spooled file on the container's own filesystem. A 200 GB upload
    therefore filled the host's docker directory and only then got its 413 —
    taking the stack down on the way.
    """
    monkeypatch.setattr("app.api.routes.backups.settings.backup_max_bytes", 1024)

    response = await client.post(
        "/api/v1/backups/upload",
        headers={
            **auth,
            "Content-Length": str(50 * 1024 * 1024),
            "Content-Type": "multipart/form-data; boundary=x",
        },
        content=b"",
    )

    assert response.status_code == 413
    assert list(backup_dir.iterdir()) == []


async def test_the_restore_status_does_not_need_the_database(client, auth, monkeypatch) -> None:
    """The one route kept open during a restore must not read the database.

    Authenticating through `users` means waiting on the lock pg_restore holds —
    so the route the blocking screen polls would hang for the whole swap, which
    is exactly when it is needed. The token is signed; decoding it is enough
    here, and the window is seconds.
    """
    import app.api.routes.backups as routes

    def explode(*args, **kwargs):
        raise AssertionError("restore-status must not touch the database")

    monkeypatch.setattr(routes, "SessionDep", None, raising=False)
    monkeypatch.setattr("app.services.user.get_by_id", explode)

    response = await client.get("/api/v1/backups/restore-status", headers=auth)

    assert response.status_code == 200
    assert response.json()["running"] is False


async def test_the_restore_status_still_refuses_a_stranger(client) -> None:
    assert (await client.get("/api/v1/backups/restore-status")).status_code == 401
