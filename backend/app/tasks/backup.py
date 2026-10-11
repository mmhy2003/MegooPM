"""Taking and restoring backups, off the request path.

A backup runs pg_dump over the whole database and writes gigabytes; a restore
replaces the database the API is talking to. Neither belongs in a request, and
neither may overlap the other — both hold the same Redis lock.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import redis
from sqlalchemy import Connection, select, update

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.enums import BackupFrequency
from app.models.instance_settings import InstanceSettings
from app.services.backup import gate, store
from app.services.backup.runner import (
    PgTools,
    create_backup,
    real_pg_tools,
    restore_backup,
)
from app.services.backup.schedule import is_backup_due
from app.services.cluster import sync_engine
from app.services.events import Event, publish

log = logging.getLogger(__name__)

BACKUP_LOCK_KEY = "megoopm:backup"
#: A large instance's dump plus the encrypt pass; well under this.
_LOCK_TIMEOUT_S = 3600


@dataclass(frozen=True, slots=True)
class BackupSettings:
    frequency: BackupFrequency
    weekday: int
    hour_utc: int
    keep: int
    last_backup_at: datetime | None


def _now() -> datetime:
    return datetime.now(UTC)


def _lock_client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url)


def _run_locked(key: str, fn: Callable[[], dict]) -> dict:
    """Run ``fn`` under a Redis lock, or report that it is already running."""
    client = _lock_client()
    lock = client.lock(key, timeout=_LOCK_TIMEOUT_S)
    try:
        if not lock.acquire(blocking=False):
            return {"ran": False, "reason": "already running"}
        try:
            return fn()
        finally:
            lock.release()
    finally:
        client.close()


def _load_backup_settings(conn: Connection) -> BackupSettings:
    table = InstanceSettings.__table__
    row = conn.execute(select(table).where(table.c.id == 1)).one()
    return BackupSettings(
        frequency=BackupFrequency(row.backup_frequency),
        weekday=row.backup_weekday,
        hour_utc=row.backup_hour_utc,
        keep=row.backup_keep,
        last_backup_at=row.last_backup_at,
    )


def _record_outcome(ok: bool, message: str | None, at: datetime) -> None:
    """Write the last run where the page reads it. Never raises."""
    try:
        engine = sync_engine()
        try:
            with engine.begin() as conn:
                conn.execute(
                    update(InstanceSettings.__table__)
                    .where(InstanceSettings.__table__.c.id == 1)
                    .values(last_backup_ok=ok, last_backup_at=at, last_backup_message=message)
                )
        finally:
            engine.dispose()
    except Exception as exc:  # noqa: BLE001 - a backup that ran must not fail on bookkeeping
        log.warning("could not record the backup outcome: %s", exc)


def _announce(event_type: str) -> None:
    """Tell connected browsers a backup or restore moved, so the page updates.

    Never raises: an announcement is not worth failing the backup it describes.
    In a worker this task is synchronous, so ``asyncio.run`` nests nothing. A running loop
    here means the task was called inline from async code — Celery's eager
    mode — where starting another is refused; the page still catches up on
    its next poll, so the announcement is simply skipped.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass  # no loop running: the normal worker case
    else:
        log.debug("skipping %s announcement: called inside a running event loop", event_type)
        return
    try:
        asyncio.run(publish(Event(type=event_type, at=datetime.now(UTC))))
    except Exception as exc:  # noqa: BLE001 - announcing must not break the backup
        log.warning("could not announce %s: %s", event_type, exc)


def _crowdsec_files() -> dict[str, Path]:
    return {
        "whitelists.yaml": Path(settings.crowdsec_whitelist_path),
        "config.yaml.local": Path(settings.crowdsec_config_local_path),
    }


def _certs_dir() -> Path | None:
    return Path(settings.nginx_certs_dir) if settings.nginx_certs_dir else None


def _known_revisions() -> set[str]:
    """Every revision this build knows, so a newer backup can be refused."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    return {revision.revision for revision in script.walk_revisions()}


def _create(trigger: str) -> dict:
    started = _now()
    # Before the space check, not after: a worker killed mid-backup leaves a
    # .part nothing else can remove, and enough of them stop backups entirely.
    swept_parts = store.sweep_interrupted(Path(settings.backup_dir))
    if swept_parts:
        log.info("swept interrupted backups: %s", ", ".join(swept_parts))
    try:
        path = create_backup(
            backup_dir=Path(settings.backup_dir),
            secret_key=settings.secret_key,
            trigger=trigger,
            now=started,
            certs_dir=_certs_dir(),
            crowdsec_files=_crowdsec_files(),
            pg=real_pg_tools(settings.sync_database_url),
            app_version=version("megoopm-backend"),
        )
    except Exception as exc:  # noqa: BLE001 - the page has to be able to show why
        log.warning("backup failed: %s", exc)
        _record_outcome(False, str(exc), started)
        _announce("backup.failed")
        return {"ran": True, "ok": False, "error": str(exc)}

    swept: list[str] = []
    if trigger == "auto":
        engine = sync_engine()
        try:
            with engine.connect() as conn:
                keep = _load_backup_settings(conn).keep
        finally:
            engine.dispose()
        swept = store.apply_retention(Path(settings.backup_dir), keep=keep)

    _record_outcome(True, None, started)
    _announce("backup.finished")
    return {"ran": True, "ok": True, "name": path.name, "swept": swept}


def _restore(name: str) -> dict:
    client = _lock_client()
    gate.begin_restore(client, "verifying")
    _announce("restore.started")
    replaced = False
    try:
        # Inside the gate: resolving the name used to happen before it, so an
        # unknown name escaped the task and the page waited forever.
        path = store.safe_path(Path(settings.backup_dir), name)

        def _restored(src: Path) -> None:
            nonlocal replaced
            replaced = True
            real_pg.restore(src)

        real_pg = real_pg_tools(settings.sync_database_url)
        meta = restore_backup(
            path=path,
            secret_key=settings.secret_key,
            certs_dir=_certs_dir(),
            crowdsec_files=_crowdsec_files(),
            pg=PgTools(dump=real_pg.dump, restore=_restored, revision=real_pg.revision),
            known_revisions=_known_revisions(),
            on_step=lambda step: gate.set_step(client, step),
        )
        gate.set_step(client, "running migrations")
        # A backup can predate the running code; the restored database has to
        # catch up before the API is allowed to talk to it again.
        subprocess.run(["alembic", "upgrade", "head"], cwd="/app", check=True)
    except Exception as exc:  # noqa: BLE001 - the blocking screen shows this
        log.warning("restore failed: %s", exc)
        if replaced:
            # The database was already swapped, so the API's pooled connections
            # hold prepared statements for a schema that no longer exists.
            # Without this it answers 500 to everything until it restarts.
            gate.mark_restored(client)
        gate.end_restore(client, error=str(exc))
        client.close()
        _announce("restore.failed")
        return {"ran": True, "ok": False, "error": str(exc), "database_replaced": replaced}

    gate.mark_restored(client)
    gate.end_restore(client)
    client.close()
    # Rebuild the generated nginx configuration from the restored database.
    from app.tasks.nginx import reload_nginx_config

    reload_nginx_config.delay()
    _announce("restore.finished")
    return {"ran": True, "ok": True, "restored_from": meta.created_at.isoformat()}


@celery_app.task(name="app.tasks.backup.create_backup_task")
def create_backup_task(trigger: str = "manual") -> dict:
    """Take one backup, record the outcome, sweep old automatic ones."""
    outcome = _run_locked(BACKUP_LOCK_KEY, lambda: _create(trigger))
    if not outcome.get("ran"):
        # The route already answered 202. Without this the refusal is invisible
        # and a skipped backup looks exactly like one that ran.
        _record_outcome(False, f"skipped: {outcome.get('reason', 'already running')}", _now())
    return outcome


@celery_app.task(name="app.tasks.backup.restore_backup_task")
def restore_backup_task(name: str) -> dict:
    """Replace this instance from a backup. Holds the backup lock throughout."""
    return _run_locked(BACKUP_LOCK_KEY, lambda: _restore(name))


@celery_app.task(name="app.tasks.backup.backup_tick")
def backup_tick() -> dict:
    """Hourly: take a scheduled backup if this is its hour."""
    now = _now()
    engine = sync_engine()
    try:
        with engine.connect() as conn:
            config = _load_backup_settings(conn)
    finally:
        engine.dispose()

    due, reason = is_backup_due(
        now=now,
        frequency=config.frequency,
        weekday=config.weekday,
        hour_utc=config.hour_utc,
        last_backup_at=config.last_backup_at,
    )
    if not due:
        return {"ran": False, "reason": reason}
    return _run_locked(BACKUP_LOCK_KEY, lambda: _create("auto"))
