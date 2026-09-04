"""CrowdSec LAPI endpoints (admin-only) — MEG-22.

Exposes the security engine to the frontend:

* ``GET  /crowdsec/health``    — is the integration configured and reachable?
* ``GET  /crowdsec/decisions`` — active decisions the bouncer enforces.
* ``GET  /crowdsec/alerts``    — recent detections CrowdSec raised.
* ``POST /crowdsec/decisions`` — push a manual (operator) ban.
* ``DELETE /crowdsec/decisions/{id}`` — lift a decision.

All routes are admin-gated: CrowdSec controls edge blocking, so reading or
mutating decisions is privileged. Missing credentials surface as ``503`` (the
integration is optional per deployment); upstream LAPI failures surface as
``502`` so the frontend can distinguish "not set up" from "set up but broken".
Manual decisions are recorded in the audit log.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import AdminUser, SessionDep
from app.core.celery_app import celery_app, node_queue
from app.core.config import settings
from app.core.redis import redis_client
from app.models.crowdsec_job_run import CrowdSecJobRun
from app.models.crowdsec_whitelist import CrowdSecWhitelist, CrowdSecWhitelistApply
from app.models.enums import AuditAction, CrowdSecJobKind
from app.schemas.crowdsec import (
    AlertList,
    CrowdSecHealth,
    CrowdSecJobRunRead,
    CrowdSecMaintenance,
    Decision,
    DecisionCreate,
    DecisionList,
)
from app.schemas.crowdsec_whitelist import (
    WhitelistApplyStatus,
    WhitelistCreate,
    WhitelistPreview,
    WhitelistRead,
    WhitelistUpdate,
)
from app.schemas.events import Event
from app.services import audit as audit_service
from app.services.crowdsec import (
    CrowdSecClient,
    CrowdSecError,
    get_crowdsec_client,
)
from app.services.crowdsec.filtering import (
    ALERT_FETCH_CAP,
    is_community_alert,
    is_community_decision,
    matches_alert,
    matches_decision,
    normalise_query,
    paginate,
)
from app.services.crowdsec.geo import enrich_alerts, enrich_decisions
from app.services.crowdsec.whitelists import (
    WhitelistDoc,
    WhitelistValidationError,
    render_whitelists,
    slugify,
)
from app.services.events import publish
from app.tasks.crowdsec import CAPI_LOCK_KEY, HUB_LOCK_KEY

router = APIRouter(tags=["crowdsec"])

ClientDep = Annotated[CrowdSecClient, Depends(get_crowdsec_client)]

# Pagination bounds shared by the two list endpoints.
_MAX_PAGE_SIZE = 200
PageArg = Annotated[int, Query(ge=1, description="1-based page number")]
PageSizeArg = Annotated[
    int, Query(ge=1, le=_MAX_PAGE_SIZE, description="Records per page (max 200)")
]
CommunityArg = Annotated[
    bool,
    Query(description="Include community/CAPI/blocklist-origin records (default: local only)"),
]
QueryArg = Annotated[
    str | None,
    Query(description="Case-insensitive substring filter, applied before pagination"),
]


def _handle(exc: CrowdSecError) -> HTTPException:
    """Map a client error onto the right HTTP status for the frontend.

    Every failure is a 503 (integration unavailable), never a 502: CDNs such as
    Cloudflare replace origin 502/504 responses with their own error page,
    dropping the JSON detail and the CORS headers, so the browser only sees a
    generic "Failed to fetch". A 503 passes through intact.
    """
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))


@router.get("/health", response_model=CrowdSecHealth)
async def crowdsec_health(_admin: AdminUser, client: ClientDep) -> CrowdSecHealth:
    """Report whether LAPI is configured, reachable, and has our machine (never errors)."""
    s = client.settings  # settings snapshot on the request-scoped client
    machine_registered = bool(s.crowdsec_machine_id and s.crowdsec_machine_password)
    configured = bool(s.crowdsec_lapi_key or machine_registered)
    if not configured:
        return CrowdSecHealth(
            configured=False,
            reachable=False,
            machine_registered=False,
            lapi_url=s.crowdsec_lapi_url,
            detail="No CrowdSec credentials configured.",
        )
    try:
        await client.ping()
    except CrowdSecError as exc:
        return CrowdSecHealth(
            configured=True,
            reachable=False,
            machine_registered=machine_registered,
            lapi_url=s.crowdsec_lapi_url,
            detail=str(exc),
        )
    detail = None
    if not machine_registered:
        detail = (
            "No LAPI machine is registered for this deployment yet: decisions are "
            "readable, but alerts and manual bans need the machine login. The backend "
            "self-registers on startup and on the next request; check that LAPI is "
            "reachable and CROWDSEC_REGISTRATION_TOKEN matches the LAPI's "
            "auto_registration token."
        )
    return CrowdSecHealth(
        configured=True,
        reachable=True,
        machine_registered=machine_registered,
        lapi_url=s.crowdsec_lapi_url,
        detail=detail,
    )


@router.get("/decisions", response_model=DecisionList)
async def list_decisions(
    _admin: AdminUser,
    client: ClientDep,
    page: PageArg = 1,
    page_size: PageSizeArg = 50,
    include_community: CommunityArg = False,
    q: QueryArg = None,
) -> DecisionList:
    """List active decisions, paginated. Hides community origins by default."""
    try:
        items = await client.list_decisions()
    except CrowdSecError as exc:
        raise _handle(exc) from exc
    items = enrich_decisions(items)
    if not include_community:
        items = [d for d in items if not is_community_decision(d)]
    # Before paginate, deliberately: filtering the page instead of the whole
    # set would report "no matches" while the match sat on page 3, and would
    # leave ``total`` offering pages that no longer exist.
    needle = normalise_query(q)
    if needle:
        items = [d for d in items if matches_decision(d, needle)]
    page_items, total = paginate(items, page=page, page_size=page_size)
    return DecisionList(items=page_items, total=total, page=page, page_size=page_size)


@router.get("/alerts", response_model=AlertList)
async def list_alerts(
    _admin: AdminUser,
    client: ClientDep,
    page: PageArg = 1,
    page_size: PageSizeArg = 50,
    include_community: CommunityArg = False,
    q: QueryArg = None,
) -> AlertList:
    """List recent alerts (newest first), paginated. Hides community by default.

    Up to ``ALERT_FETCH_CAP`` alerts are fetched from LAPI before server-side
    filtering/pagination, so ``total`` is relative to that bounded window.
    """
    try:
        items = await client.list_alerts(limit=ALERT_FETCH_CAP)
    except CrowdSecError as exc:
        raise _handle(exc) from exc
    items = enrich_alerts(items)
    if not include_community:
        items = [a for a in items if not is_community_alert(a)]
    needle = normalise_query(q)
    if needle:
        items = [a for a in items if matches_alert(a, needle)]
    page_items, total = paginate(items, page=page, page_size=page_size)
    return AlertList(items=page_items, total=total, page=page, page_size=page_size)


@router.post("/decisions", response_model=Decision, status_code=status.HTTP_201_CREATED)
async def add_decision(
    admin: AdminUser, client: ClientDep, db: SessionDep, payload: DecisionCreate
) -> Decision:
    """Push a manual decision (operator ban) and record it in the audit log."""
    try:
        decision = await client.add_decision(payload)
    except CrowdSecError as exc:
        raise _handle(exc) from exc
    await audit_service.record_audit(
        db,
        actor=admin.email,
        action=AuditAction.create,
        object_type="crowdsec_decision",
        meta={
            "scope": payload.scope,
            "value": payload.value,
            "type": payload.type,
            "duration": payload.duration,
            "reason": payload.reason,
        },
    )
    await db.commit()

    # Only decisions MegooPM itself creates. Bans CrowdSec raises on its own
    # are invisible until the next poll, because nothing tells MegooPM they
    # happened — see the spec; an operator who believes the map is live would
    # misread a quiet globe.
    await publish(
        Event(
            type="decision.added",
            at=datetime.now(UTC),
            detail={"value": payload.value, "scope": payload.scope},
        )
    )
    return decision


@router.delete("/decisions/{decision_id}", status_code=status.HTTP_200_OK)
async def delete_decision(
    admin: AdminUser, client: ClientDep, db: SessionDep, decision_id: int
) -> dict[str, int]:
    """Lift a decision by id; records the removal in the audit log."""
    try:
        deleted = await client.delete_decision(decision_id)
    except CrowdSecError as exc:
        raise _handle(exc) from exc
    await audit_service.record_audit(
        db,
        actor=admin.email,
        action=AuditAction.delete,
        object_type="crowdsec_decision",
        object_id=decision_id,
        meta={"deleted": deleted},
    )
    await db.commit()
    return {"deleted": deleted}


# --- whitelists ------------------------------------------------------------
#
# UI-authored CrowdSec parser whitelists. Writing the file and reloading
# CrowdSec happens in a Celery task on the control-plane node (only that node
# runs the CrowdSec container, and only its worker has the docker socket), so
# these routes persist and enqueue; ``GET /whitelists/status`` reports whether
# the apply actually landed.


def _reload_configured() -> bool:
    """Whether an apply has a worker that will actually run it.

    Single node (``HA_ENABLED=false``): there is one worker on the default
    queue, and it is the one with the docker socket. Nothing to address, so
    reloads are always configured.

    HA: workers consume their own ``megoopm.node.<id>`` queues, and only the
    control-plane node runs the CrowdSec container and holds the socket. That
    node has to be named, or there is nowhere to send the task.
    """
    return not settings.ha_enabled or bool(settings.crowdsec_control_node_id)


RELOADS_NOT_CONFIGURED = (
    "CrowdSec reloads are not configured: set CROWDSEC_CONTROL_NODE_ID "
    "to the node whose worker has the docker socket (HA only; a "
    "single-node deployment needs no node id)."
)


def reload_configured() -> bool:
    return _reload_configured()


def enqueue_control_task(name: str, **kwargs) -> bool:
    """Send ``name`` to the worker that holds the docker socket, or say it cannot.

    Returning False rather than enqueueing blindly matters: under HA a task
    addressed to a queue no worker consumes sits there forever, and the
    operator would see a change that never takes effect with nothing
    anywhere explaining why.
    """
    if not _reload_configured():
        return False
    if not settings.ha_enabled:
        # Default queue: the single worker consumes it. Addressing a per-node
        # queue here would be worse than useless — `_configure_ha` never ran,
        # so nothing is listening on it.
        celery_app.send_task(name, **kwargs)
        return True
    celery_app.send_task(name, queue=node_queue(settings.crowdsec_control_node_id), **kwargs)
    return True


def _enqueue_apply() -> bool:
    """Queue the whitelist apply. False when no worker would run it."""
    return enqueue_control_task("app.tasks.crowdsec.apply_crowdsec_whitelists")


async def _job_running(key: str) -> bool:
    client = redis_client()
    try:
        return bool(await client.exists(key))
    finally:
        await client.aclose()


async def _guard_slug_unique(db: SessionDep, name: str, *, exclude_id: int | None) -> None:
    """409 when two names would render the same CrowdSec ``name:``.

    CrowdSec requires parser names to be unique across everything it loads, and
    a duplicate makes it refuse to start — so this is caught here rather than
    discovered when the container fails to come back.
    """
    try:
        slug = slugify(name)
    except WhitelistValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    result = await db.execute(select(CrowdSecWhitelist))
    for row in result.scalars():
        if row.id != exclude_id and slugify(row.name) == slug:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Whitelist {row.name!r} already renders as megoopm/wl-{slug}.",
            )


@router.get("/whitelists", response_model=list[WhitelistRead])
async def list_whitelists(_: AdminUser, db: SessionDep) -> list[CrowdSecWhitelist]:
    """Every whitelist, enabled or not, oldest first."""
    result = await db.execute(select(CrowdSecWhitelist).order_by(CrowdSecWhitelist.id))
    return list(result.scalars())


@router.post("/whitelists", response_model=WhitelistRead, status_code=status.HTTP_201_CREATED)
async def create_whitelist(
    admin: AdminUser, db: SessionDep, payload: WhitelistCreate
) -> CrowdSecWhitelist:
    """Create a whitelist and queue the apply."""
    await _guard_slug_unique(db, payload.name, exclude_id=None)
    row = CrowdSecWhitelist(**payload.model_dump())
    db.add(row)
    await db.flush()
    await audit_service.record_audit(
        db,
        actor=admin.email,
        action=AuditAction.create,
        object_type="crowdsec_whitelist",
        object_id=row.id,
        meta={"name": row.name, "kind": str(row.kind)},
    )
    await db.commit()
    await db.refresh(row)
    _enqueue_apply()
    return row


@router.patch("/whitelists/{whitelist_id}", response_model=WhitelistRead)
async def update_whitelist(
    admin: AdminUser, db: SessionDep, whitelist_id: int, payload: WhitelistUpdate
) -> CrowdSecWhitelist:
    """Replace a whitelist and queue the apply."""
    row = await db.get(CrowdSecWhitelist, whitelist_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Whitelist not found.")
    await _guard_slug_unique(db, payload.name, exclude_id=whitelist_id)
    for field, value in payload.model_dump().items():
        setattr(row, field, value)
    await audit_service.record_audit(
        db,
        actor=admin.email,
        action=AuditAction.update,
        object_type="crowdsec_whitelist",
        object_id=row.id,
        meta={"name": row.name, "enabled": row.enabled},
    )
    await db.commit()
    await db.refresh(row)
    _enqueue_apply()
    return row


@router.delete("/whitelists/{whitelist_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_whitelist(admin: AdminUser, db: SessionDep, whitelist_id: int) -> None:
    """Delete a whitelist and queue the apply."""
    row = await db.get(CrowdSecWhitelist, whitelist_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Whitelist not found.")
    name = row.name
    await db.delete(row)
    await audit_service.record_audit(
        db,
        actor=admin.email,
        action=AuditAction.delete,
        object_type="crowdsec_whitelist",
        object_id=whitelist_id,
        meta={"name": name},
    )
    await db.commit()
    _enqueue_apply()


@router.post("/whitelists/preview", response_model=WhitelistPreview)
async def preview_whitelist(_: AdminUser, payload: WhitelistCreate) -> WhitelistPreview:
    """Render one whitelist exactly as the writer would.

    The dialog shows this rather than re-implementing the renderer in
    TypeScript: a second renderer would drift, and the preview's whole value is
    being the same bytes that reach CrowdSec.
    """
    doc = WhitelistDoc(
        name=payload.name,
        reason=payload.reason,
        description=payload.description,
        ips=payload.ips,
        cidrs=payload.cidrs,
        kind=str(payload.kind),
        filter=payload.filter,
        expressions=payload.expressions,
    )
    try:
        return WhitelistPreview(yaml=render_whitelists([doc]))
    except WhitelistValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/whitelists/status", response_model=WhitelistApplyStatus)
async def whitelist_status(_: AdminUser, db: SessionDep) -> WhitelistApplyStatus:
    """Whether the last apply reached CrowdSec, and whether reloads are wired."""
    row = await db.get(CrowdSecWhitelistApply, 1)
    configured = _reload_configured()
    if row is None:
        return WhitelistApplyStatus(ok=True, reload_configured=configured)
    return WhitelistApplyStatus(
        ok=row.ok,
        error=row.error,
        applied_at=row.applied_at,
        reload_configured=configured,
    )


@router.post("/whitelists/apply", status_code=status.HTTP_202_ACCEPTED)
async def apply_whitelists(_: AdminUser) -> dict[str, bool]:
    """Re-run the apply — the retry path after a failed reload."""
    if not _enqueue_apply():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=RELOADS_NOT_CONFIGURED,
        )
    return {"queued": True}


# --- maintenance: the Updates tab ---------------------------------------------------


@router.get("/maintenance", response_model=CrowdSecMaintenance)
async def maintenance(_: AdminUser, db: SessionDep) -> CrowdSecMaintenance:
    """Both maintenance jobs' last runs, and whether one is running now."""
    hub_row = await db.get(CrowdSecJobRun, CrowdSecJobKind.hub_update)
    capi_row = await db.get(CrowdSecJobRun, CrowdSecJobKind.capi_apply)
    return CrowdSecMaintenance(
        hub=CrowdSecJobRunRead.model_validate(hub_row) if hub_row else None,
        capi=CrowdSecJobRunRead.model_validate(capi_row) if capi_row else None,
        reload_configured=_reload_configured(),
        running={
            "hub": await _job_running(HUB_LOCK_KEY),
            "capi": await _job_running(CAPI_LOCK_KEY),
        },
    )


@router.post("/hub/update", status_code=status.HTTP_202_ACCEPTED)
async def hub_update_now(admin: AdminUser, db: SessionDep) -> dict[str, bool]:
    """Refresh the hub now. 409 while a run is in progress or reloads are unwired."""
    if await _job_running(HUB_LOCK_KEY):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="An update is already running."
        )
    if not enqueue_control_task("app.tasks.crowdsec.update_hub", kwargs={"trigger": "manual"}):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=RELOADS_NOT_CONFIGURED)
    await audit_service.record_audit(
        db,
        actor=admin.email,
        action=AuditAction.update,
        object_type="crowdsec_hub",
        meta={"update_now": True},
    )
    await db.commit()
    return {"queued": True}


__all__ = ["RELOADS_NOT_CONFIGURED", "enqueue_control_task", "reload_configured", "router"]
