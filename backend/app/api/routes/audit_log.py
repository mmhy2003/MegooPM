"""Audit-log read routes.

``GET /audit-log`` exposes the append-only audit trail: newest-first, paginated,
and filterable by ``object_type``/``object_id``/``actor``/``action``. Access is
admin-only (RBAC) — the trail can reveal who changed privileged infrastructure.

There is no write route: audit rows are produced by
``app/services/audit.py::record_audit`` from within privileged mutation
handlers, never by an external caller.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, SessionDep
from app.models.enums import AuditAction
from app.schemas.audit import AuditLogPage, AuditLogRead
from app.services import audit as audit_service

router = APIRouter(tags=["audit-log"])


@router.get("", response_model=AuditLogPage)
async def list_audit_log(
    _admin: AdminUser,
    db: SessionDep,
    object_type: Annotated[
        str | None, Query(description="Filter by object type, e.g. proxy_host")
    ] = None,
    object_id: Annotated[
        int | None, Query(description="Filter by the mutated object's id")
    ] = None,
    actor: Annotated[str | None, Query(description="Filter by actor (exact match)")] = None,
    action: Annotated[AuditAction | None, Query(description="Filter by mutation action")] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="Max entries to return")] = 50,
    offset: Annotated[int, Query(ge=0, description="Entries to skip (pagination)")] = 0,
) -> AuditLogPage:
    """List audit-log entries, newest first. Admin-only."""
    rows, total = await audit_service.list_audit_logs(
        db,
        object_type=object_type,
        object_id=object_id,
        actor=actor,
        action=action,
        limit=limit,
        offset=offset,
    )
    return AuditLogPage(
        items=[AuditLogRead.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
