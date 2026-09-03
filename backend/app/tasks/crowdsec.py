"""Apply UI-authored CrowdSec whitelists and reload CrowdSec.

The pure part — :func:`apply_whitelists_to_disk` — takes the restart and the
health check as callables, so the write / restart / rollback sequence is
testable without a docker socket or a running CrowdSec.

Restarting CrowdSec makes AppSec briefly unreachable, and the bouncer runs
``APPSEC_FAILURE_ACTION=deny``, so every ``crowdsec_enabled`` host fails closed
for the duration. Two consequences are load bearing rather than polish:

* an unchanged render must not restart anything — hence the digest
  short-circuit; and
* a file CrowdSec cannot load must be rolled back, because otherwise that
  few-second denial lasts until a human intervenes.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import redis
from sqlalchemy import Connection, select

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.crowdsec_whitelist import CrowdSecWhitelist
from app.models.enums import CrowdSecJobKind, CrowdSecJobTrigger, HubUpdateFrequency
from app.models.instance_settings import InstanceSettings
from app.services.cluster import sync_engine
from app.services.crowdsec import CrowdSecClient, CrowdSecError, capi, hub
from app.services.crowdsec.apply_state import read_apply_state, record_apply
from app.services.crowdsec.job_run import finish_job_run, read_job_run, start_job_run
from app.services.crowdsec.reload import (
    CrowdSecReloadError,
    ExecResult,
    exec_in_container,
    restart_container,
)
from app.services.crowdsec.whitelists import (
    WhitelistDoc,
    WhitelistValidationError,
    content_digest,
    read_whitelist_file,
    render_whitelists,
    write_whitelist_file,
)


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """Outcome of one apply, JSON-serialisable via :meth:`as_dict`."""

    ok: bool
    digest: str | None
    error: str | None
    restarted: bool

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "digest": self.digest,
            "error": self.error,
            "restarted": self.restarted,
        }


def apply_whitelists_to_disk(
    docs: Sequence[WhitelistDoc],
    *,
    path: Path,
    applied_digest: str | None,
    restart: Callable[[], None],
    healthy: Callable[[], bool],
) -> ApplyResult:
    """Render, write in place, restart, verify — and roll back if it fails."""
    try:
        content = render_whitelists(docs)
    except WhitelistValidationError as exc:
        # Never write: this file is the thing that can stop CrowdSec starting.
        return ApplyResult(ok=False, digest=None, error=str(exc), restarted=False)

    digest = content_digest(content)
    previous = read_whitelist_file(path)
    if digest == applied_digest and previous == content:
        return ApplyResult(ok=True, digest=digest, error=None, restarted=False)

    write_whitelist_file(path, content)

    try:
        restart()
    except CrowdSecReloadError as exc:
        write_whitelist_file(path, previous)
        return ApplyResult(ok=False, digest=None, error=str(exc), restarted=False)

    if healthy():
        return ApplyResult(ok=True, digest=digest, error=None, restarted=True)

    # CrowdSec did not answer again. The likeliest cause is a parser file it
    # cannot load, which leaves every protected host failing closed, so put the
    # last known-good content back and restart onto that.
    write_whitelist_file(path, previous)
    try:
        restart()
    except CrowdSecReloadError as exc:
        return ApplyResult(
            ok=False,
            digest=None,
            error=(
                "CrowdSec did not come back after the whitelist change, and the "
                f"rollback restart also failed: {exc}"
            ),
            restarted=True,
        )
    return ApplyResult(
        ok=False,
        digest=None,
        error=(
            "CrowdSec did not come back within "
            f"{settings.crowdsec_reload_health_timeout_seconds}s of the whitelist "
            "change. The previous whitelist file has been restored."
        ),
        restarted=True,
    )


def _load_docs(conn: Connection) -> list[WhitelistDoc]:
    """Every enabled whitelist, in id order so the render is byte-stable."""
    table = CrowdSecWhitelist.__table__
    rows = conn.execute(select(table).where(table.c.enabled.is_(True)).order_by(table.c.id)).all()
    return [
        WhitelistDoc(
            name=row.name,
            reason=row.reason,
            description=row.description,
            ips=list(row.ips),
            cidrs=list(row.cidrs),
            kind=str(row.kind),
            filter=row.filter,
            expressions=list(row.expressions),
        )
        for row in rows
    ]


def _wait_for_lapi() -> bool:
    """Poll LAPI until it answers or the health timeout elapses."""
    deadline = time.monotonic() + settings.crowdsec_reload_health_timeout_seconds

    async def _ping() -> None:
        async with CrowdSecClient() as client:
            await client.ping()

    while time.monotonic() < deadline:
        try:
            asyncio.run(_ping())
            return True
        except (CrowdSecError, OSError):
            time.sleep(2)
    return False


@celery_app.task(name="app.tasks.crowdsec.apply_crowdsec_whitelists")
def apply_crowdsec_whitelists() -> dict:
    """Render every enabled whitelist, apply it, and record the outcome."""
    engine = sync_engine()
    try:
        with engine.begin() as conn:
            docs = _load_docs(conn)
            state = read_apply_state(conn)

        result = apply_whitelists_to_disk(
            docs,
            path=Path(settings.crowdsec_whitelist_path),
            applied_digest=state.applied_digest,
            restart=lambda: restart_container(
                settings.crowdsec_container_name,
                socket_path=settings.docker_socket_path,
                timeout_seconds=settings.crowdsec_reload_health_timeout_seconds,
            ),
            healthy=_wait_for_lapi,
        )

        with engine.begin() as conn:
            record_apply(conn, digest=result.digest, ok=result.ok, error=result.error)
        return result.as_dict()
    finally:
        engine.dispose()


# --- maintenance: hub refresh and the community blocklist ------------------------

HUB_LOCK_KEY = "megoopm:crowdsec:hub-update"
CAPI_LOCK_KEY = "megoopm:crowdsec:capi-apply"
#: Both jobs talk to the internet and restart a container; well under this.
_LOCK_TIMEOUT_S = 900
_EXEC_TIMEOUT_S = 120


@dataclass(frozen=True, slots=True)
class MaintenanceSettings:
    auto_update: bool
    frequency: HubUpdateFrequency
    weekday: int
    hour_utc: int
    capi_enabled: bool


def _load_maintenance_settings(conn: Connection) -> MaintenanceSettings:
    table = InstanceSettings.__table__
    row = conn.execute(select(table).where(table.c.id == 1)).one()
    return MaintenanceSettings(
        auto_update=row.crowdsec_hub_auto_update,
        frequency=HubUpdateFrequency(row.crowdsec_hub_update_frequency),
        weekday=row.crowdsec_hub_update_weekday,
        hour_utc=row.crowdsec_hub_update_hour_utc,
        capi_enabled=row.crowdsec_capi_enabled,
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _lock_client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url)


def _container_exec(argv: list[str]) -> ExecResult:
    return exec_in_container(
        settings.crowdsec_container_name,
        argv,
        socket_path=settings.docker_socket_path,
        timeout_seconds=_EXEC_TIMEOUT_S,
    )


def _container_restart() -> None:
    restart_container(
        settings.crowdsec_container_name,
        socket_path=settings.docker_socket_path,
        timeout_seconds=settings.crowdsec_reload_health_timeout_seconds,
    )


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


@celery_app.task(name="app.tasks.crowdsec.update_hub")
def update_hub(trigger: str = "manual") -> dict:
    """Refresh hub items; restart only if something changed; record the outcome."""

    def _go() -> dict:
        engine = sync_engine()
        try:
            with engine.begin() as conn:
                start_job_run(
                    conn,
                    CrowdSecJobKind.hub_update,
                    trigger=CrowdSecJobTrigger(trigger),
                    started_at=_now(),
                )
            result = hub.run_hub_update(
                exec=_container_exec, restart=_container_restart, healthy=_wait_for_lapi
            )
            with engine.begin() as conn:
                finish_job_run(
                    conn,
                    CrowdSecJobKind.hub_update,
                    ok=result.ok,
                    error=result.error,
                    restarted=result.restarted,
                    detail={
                        "updated": result.updated,
                        "agent_version": result.agent_version,
                        "latest_agent_version": result.latest_agent_version,
                    },
                    finished_at=_now(),
                )
            return result.as_dict()
        finally:
            engine.dispose()

    return _run_locked(HUB_LOCK_KEY, _go)


@celery_app.task(name="app.tasks.crowdsec.hub_update_tick")
def hub_update_tick() -> dict:
    """Hourly: run the hub refresh if this is the configured slot."""
    engine = sync_engine()
    try:
        with engine.begin() as conn:
            conf = _load_maintenance_settings(conn)
            last = read_job_run(conn, CrowdSecJobKind.hub_update)
    finally:
        engine.dispose()
    due, reason = hub.is_due(
        now=_now(),
        auto_update=conf.auto_update,
        frequency=conf.frequency,
        weekday=conf.weekday,
        hour_utc=conf.hour_utc,
        last_started_at=last.started_at if last else None,
    )
    if not due:
        return {"ran": False, "reason": reason}
    outcome = update_hub.run("scheduled")
    return {"ran": True, **outcome}


@celery_app.task(name="app.tasks.crowdsec.apply_capi")
def apply_capi() -> dict:
    """Make the container's config match the desired blocklist state."""

    def _go() -> dict:
        engine = sync_engine()
        try:
            with engine.begin() as conn:
                conf = _load_maintenance_settings(conn)
                start_job_run(
                    conn,
                    CrowdSecJobKind.capi_apply,
                    trigger=CrowdSecJobTrigger.manual,
                    started_at=_now(),
                )
            result = capi.run_capi_apply(
                enabled=conf.capi_enabled,
                path=Path(settings.crowdsec_config_local_path),
                exec=_container_exec,
                restart=_container_restart,
                healthy=_wait_for_lapi,
            )
            with engine.begin() as conn:
                finish_job_run(
                    conn,
                    CrowdSecJobKind.capi_apply,
                    ok=result.ok,
                    error=result.error,
                    restarted=result.restarted,
                    detail={"enabled": result.enabled},
                    finished_at=_now(),
                )
            return result.as_dict()
        finally:
            engine.dispose()

    return _run_locked(CAPI_LOCK_KEY, _go)


__all__ = [
    "CAPI_LOCK_KEY",
    "HUB_LOCK_KEY",
    "ApplyResult",
    "apply_capi",
    "apply_crowdsec_whitelists",
    "apply_whitelists_to_disk",
    "hub_update_tick",
    "update_hub",
]
