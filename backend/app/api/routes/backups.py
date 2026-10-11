"""The backups API: list, take, download, upload, delete, restore.

Admin-only throughout. Every route that takes a name resolves it through
``store.safe_path``, which refuses anything that is not a file this instance
would have written — the name comes from the URL, so that check is the boundary
between an admin and the filesystem.

Route order matters: the literal paths are declared before ``/{name}`` so
``/upload`` and ``/restore-status`` are never captured as backup names.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import redis
from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from app.api.deps import AdminUser, SessionDep
from app.core.config import settings
from app.models.enums import AuditAction
from app.schemas.backup import BackupList, BackupRead, RestoreStatus
from app.schemas.tasks import TaskEnqueued
from app.services.audit import record_audit
from app.services.backup import gate, store
from app.services.backup.crypto import BackupFormatError, read_header
from app.tasks.backup import create_backup_task, restore_backup_task

router = APIRouter(tags=["backups"])

#: Streamed in both directions; the files are far too large for one buffer.
_IO_CHUNK = 1024 * 1024


def _dir() -> Path:
    return Path(settings.backup_dir)


def _resolve(name: str) -> Path:
    try:
        return store.safe_path(_dir(), name)
    except store.UnknownBackupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("", response_model=BackupList)
async def list_backups(_admin: AdminUser) -> BackupList:
    """Every backup in the directory, newest first. Admin-only."""
    rows = store.list_backups(_dir(), secret_key=settings.secret_key)
    return BackupList(
        items=[BackupRead.from_file(row) for row in rows],
        free_bytes=store.free_bytes(_dir()) if _dir().is_dir() else 0,
    )


@router.get("/restore-status", response_model=RestoreStatus)
async def read_restore_status(_admin: AdminUser) -> RestoreStatus:
    """What a restore is doing. The one route that answers during a restore."""
    client = redis.Redis.from_url(settings.redis_url)
    try:
        return RestoreStatus(**gate.restore_status(client))
    except redis.RedisError:
        # Redis being unreachable is not evidence of a restore, and the page
        # that polls this must not error because the flag store blinked. The
        # middleware fails open the same way.
        return RestoreStatus(running=False)
    finally:
        client.close()


@router.post("", response_model=TaskEnqueued, status_code=status.HTTP_202_ACCEPTED)
async def take_backup(admin: AdminUser, db: SessionDep) -> TaskEnqueued:
    """Take a backup now. Admin-only; runs on the worker."""
    await record_audit(
        db,
        actor=admin.email,
        action=AuditAction.create,
        object_type="backup",
        meta={"trigger": "manual"},
    )
    await db.commit()
    async_result = create_backup_task.delay("manual")
    return TaskEnqueued(task_id=async_result.id, status=async_result.status)


@router.post("/upload", response_model=BackupRead, status_code=status.HTTP_201_CREATED)
async def upload_backup(
    admin: AdminUser, db: SessionDep, file: Annotated[UploadFile, File()]
) -> BackupRead:
    """Accept a backup made elsewhere. Admin-only.

    Streamed to a ``.part`` file and checked before it is given a real name: an
    oversized or malformed upload must leave nothing behind, and must never be
    listed as something an admin could restore.
    """
    _dir().mkdir(parents=True, exist_ok=True, mode=0o700)
    name = store.new_name(datetime.now(UTC), "upload")
    part = _dir() / f"{name}{store.PART_SUFFIX}"
    written = 0
    try:
        with part.open("wb") as out:
            while chunk := await file.read(_IO_CHUNK):
                written += len(chunk)
                if written > settings.backup_max_bytes:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"a backup may be at most {settings.backup_max_bytes} bytes",
                    )
                out.write(chunk)
        with part.open("rb") as handle:
            read_header(handle)
    except HTTPException:
        part.unlink(missing_ok=True)
        raise
    except BackupFormatError as exc:
        part.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except BaseException:
        part.unlink(missing_ok=True)
        raise

    final = _dir() / name
    part.replace(final)
    final.chmod(0o600)
    await record_audit(
        db,
        actor=admin.email,
        action=AuditAction.create,
        object_type="backup",
        meta={"trigger": "upload", "name": name, "bytes": written},
    )
    await db.commit()
    return BackupRead.from_file(store.describe(final, secret_key=settings.secret_key))


@router.get("/{name}/download")
async def download_backup(name: str, admin: AdminUser, db: SessionDep) -> StreamingResponse:
    """Stream one backup to the browser. Admin-only, and audited: this is the
    one route that takes TLS private keys off the server."""
    path = _resolve(name)
    await record_audit(
        db,
        actor=admin.email,
        action=AuditAction.update,
        object_type="backup",
        meta={"event": "download", "name": name},
    )
    await db.commit()

    def chunks():
        with path.open("rb") as handle:
            while chunk := handle.read(_IO_CHUNK):
                yield chunk

    return StreamingResponse(
        chunks(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Content-Length": str(path.stat().st_size),
        },
    )


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_backup(name: str, admin: AdminUser, db: SessionDep) -> None:
    """Delete one backup. Admin-only."""
    path = _resolve(name)
    path.unlink(missing_ok=True)
    await record_audit(
        db,
        actor=admin.email,
        action=AuditAction.delete,
        object_type="backup",
        meta={"name": name},
    )
    await db.commit()


@router.post("/{name}/restore", response_model=TaskEnqueued, status_code=status.HTTP_202_ACCEPTED)
async def restore_from_backup(name: str, admin: AdminUser, db: SessionDep) -> TaskEnqueued:
    """Replace this instance from a backup. Admin-only; runs on the worker.

    The audit row is written *before* the task is enqueued: the restore will
    replace the audit log itself, so this record of who asked only survives in
    the backup being restored — and in the worker log.
    """
    _resolve(name)
    await record_audit(
        db,
        actor=admin.email,
        action=AuditAction.restore,
        object_type="backup",
        meta={"name": name},
    )
    await db.commit()
    async_result = restore_backup_task.delay(name)
    return TaskEnqueued(task_id=async_result.id, status=async_result.status)
