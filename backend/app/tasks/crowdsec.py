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
from pathlib import Path

from sqlalchemy import Connection, select

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.crowdsec_whitelist import CrowdSecWhitelist
from app.services.cluster import sync_engine
from app.services.crowdsec import CrowdSecClient, CrowdSecError
from app.services.crowdsec.apply_state import read_apply_state, record_apply
from app.services.crowdsec.reload import CrowdSecReloadError, restart_container
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
    rows = conn.execute(
        select(table).where(table.c.enabled.is_(True)).order_by(table.c.id)
    ).all()
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


__all__ = ["ApplyResult", "apply_crowdsec_whitelists", "apply_whitelists_to_disk"]
