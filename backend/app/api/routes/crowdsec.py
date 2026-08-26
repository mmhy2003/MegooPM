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

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import AdminUser, SessionDep
from app.models.enums import AuditAction
from app.schemas.crowdsec import (
    AlertList,
    CrowdSecHealth,
    Decision,
    DecisionCreate,
    DecisionList,
)
from app.services import audit as audit_service
from app.services.crowdsec import (
    CrowdSecClient,
    CrowdSecError,
    CrowdSecNotConfigured,
    get_crowdsec_client,
)

router = APIRouter(tags=["crowdsec"])

ClientDep = Annotated[CrowdSecClient, Depends(get_crowdsec_client)]


def _handle(exc: CrowdSecError) -> HTTPException:
    """Map a client error onto the right HTTP status for the frontend."""
    if isinstance(exc, CrowdSecNotConfigured):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    return HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.get("/health", response_model=CrowdSecHealth)
async def crowdsec_health(_admin: AdminUser, client: ClientDep) -> CrowdSecHealth:
    """Report whether LAPI is configured and reachable (never errors)."""
    s = client.settings  # settings snapshot on the request-scoped client
    configured = bool(
        s.crowdsec_lapi_key or (s.crowdsec_machine_id and s.crowdsec_machine_password)
    )
    if not configured:
        return CrowdSecHealth(
            configured=False,
            reachable=False,
            lapi_url=s.crowdsec_lapi_url,
            detail="No CrowdSec credentials configured.",
        )
    try:
        await client.ping()
    except CrowdSecError as exc:
        return CrowdSecHealth(
            configured=True, reachable=False, lapi_url=s.crowdsec_lapi_url, detail=str(exc)
        )
    return CrowdSecHealth(configured=True, reachable=True, lapi_url=s.crowdsec_lapi_url)


@router.get("/decisions", response_model=DecisionList)
async def list_decisions(_admin: AdminUser, client: ClientDep) -> DecisionList:
    """List active decisions the bouncer enforces."""
    try:
        items = await client.list_decisions()
    except CrowdSecError as exc:
        raise _handle(exc) from exc
    return DecisionList(items=items, total=len(items))


@router.get("/alerts", response_model=AlertList)
async def list_alerts(
    _admin: AdminUser,
    client: ClientDep,
    limit: Annotated[int, Query(ge=1, le=200, description="Max alerts to return")] = 50,
) -> AlertList:
    """List recent alerts CrowdSec raised, newest first."""
    try:
        items = await client.list_alerts(limit=limit)
    except CrowdSecError as exc:
        raise _handle(exc) from exc
    return AlertList(items=items, total=len(items))


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


__all__ = ["router"]
