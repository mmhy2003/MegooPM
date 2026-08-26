"""Dead (404) host CRUD routes (admin-only).

A dead host parks a set of domains and answers every request with a 404. This
router exposes full CRUD; every mutating write records an audit entry and
enqueues an nginx regenerate-and-reload (see
:mod:`app.api.routes._config_writes`), returning the reload task id in the
``X-Config-Reload-Task`` header. A create/update referencing a non-existent
certificate is rejected with 422 before any config is generated.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import AdminUser, SessionDep
from app.api.routes._config_writes import after_config_write
from app.models.enums import AuditAction
from app.schemas.dead_host import DeadHostCreate, DeadHostRead, DeadHostUpdate
from app.services import dead_host as dead_host_service

router = APIRouter(tags=["dead-hosts"])


@router.get("", response_model=list[DeadHostRead])
async def list_dead_hosts(_admin: AdminUser, db: SessionDep) -> list[DeadHostRead]:
    """List all dead hosts. Admin-only."""
    hosts = await dead_host_service.list_dead_hosts(db)
    return [DeadHostRead.model_validate(h) for h in hosts]


@router.post("", response_model=DeadHostRead, status_code=status.HTTP_201_CREATED)
async def create_dead_host(
    body: DeadHostCreate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> DeadHostRead:
    """Create a dead host. Admin-only."""
    try:
        host = await dead_host_service.create_dead_host(db, body.model_dump())
    except dead_host_service.InvalidReferenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.create,
        object_type="dead_host",
        object_id=host.id,
        meta={"domain_names": host.domain_names},
    )
    return DeadHostRead.model_validate(host)


@router.get("/{host_id}", response_model=DeadHostRead)
async def get_dead_host(host_id: int, _admin: AdminUser, db: SessionDep) -> DeadHostRead:
    """Fetch a single dead host. Admin-only."""
    host = await dead_host_service.get_dead_host(db, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dead host not found")
    return DeadHostRead.model_validate(host)


@router.patch("/{host_id}", response_model=DeadHostRead)
async def update_dead_host(
    host_id: int,
    body: DeadHostUpdate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> DeadHostRead:
    """Update a dead host. Admin-only."""
    changes = body.model_dump(exclude_unset=True)
    try:
        host = await dead_host_service.update_dead_host(db, host_id, changes)
    except dead_host_service.DeadHostNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Dead host not found"
        ) from None
    except dead_host_service.InvalidReferenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.update,
        object_type="dead_host",
        object_id=host.id,
        meta={"changed": sorted(changes)},
    )
    return DeadHostRead.model_validate(host)


@router.delete("/{host_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dead_host(
    host_id: int,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> Response:
    """Delete a dead host. Admin-only."""
    try:
        await dead_host_service.delete_dead_host(db, host_id)
    except dead_host_service.DeadHostNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Dead host not found"
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.delete,
        object_type="dead_host",
        object_id=host_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=response.headers)


__all__ = ["router"]
