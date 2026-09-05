"""Redirection-host CRUD routes (admin-only).

A redirection host claims a set of domains and answers every request with an
HTTP redirect to a target domain. This router exposes full CRUD; every mutating
write records an audit entry and enqueues an nginx regenerate-and-reload (see
:mod:`app.api.routes._config_writes`), returning the reload task id in the
``X-Config-Reload-Task`` header. A create/update referencing a non-existent
certificate is rejected with 422 before any config is generated.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import AdminUser, SessionDep
from app.api.routes._config_writes import after_config_write
from app.models.enums import AuditAction
from app.schemas.redirection_host import (
    RedirectionHostCreate,
    RedirectionHostRead,
    RedirectionHostUpdate,
)
from app.services import redirection_host as redirection_host_service

router = APIRouter(tags=["redirection-hosts"])


@router.get("", response_model=list[RedirectionHostRead])
async def list_redirection_hosts(_admin: AdminUser, db: SessionDep) -> list[RedirectionHostRead]:
    """List all redirection hosts. Admin-only."""
    hosts = await redirection_host_service.list_redirection_hosts(db)
    return [RedirectionHostRead.model_validate(h) for h in hosts]


@router.post("", response_model=RedirectionHostRead, status_code=status.HTTP_201_CREATED)
async def create_redirection_host(
    body: RedirectionHostCreate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> RedirectionHostRead:
    """Create a redirection host. Admin-only."""
    try:
        host = await redirection_host_service.create_redirection_host(db, body.model_dump())
    except redirection_host_service.InvalidReferenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.create,
        object_type="redirection_host",
        object_id=host.id,
        meta={"domain_names": host.domain_names, "forward_domain_name": host.forward_domain_name},
    )
    return RedirectionHostRead.model_validate(host)


@router.get("/{host_id}", response_model=RedirectionHostRead)
async def get_redirection_host(
    host_id: int, _admin: AdminUser, db: SessionDep
) -> RedirectionHostRead:
    """Fetch a single redirection host. Admin-only."""
    host = await redirection_host_service.get_redirection_host(db, host_id)
    if host is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Redirection host not found"
        )
    return RedirectionHostRead.model_validate(host)


@router.patch("/{host_id}", response_model=RedirectionHostRead)
async def update_redirection_host(
    host_id: int,
    body: RedirectionHostUpdate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> RedirectionHostRead:
    """Update a redirection host. Admin-only."""
    changes = body.model_dump(exclude_unset=True)
    try:
        host = await redirection_host_service.update_redirection_host(db, host_id, changes)
    except redirection_host_service.RedirectionHostNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Redirection host not found"
        ) from None
    except redirection_host_service.InvalidReferenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.update,
        object_type="redirection_host",
        object_id=host.id,
        meta={"changed": sorted(changes)},
    )
    return RedirectionHostRead.model_validate(host)


@router.delete("/{host_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_redirection_host(
    host_id: int,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> Response:
    """Delete a redirection host. Admin-only."""
    try:
        await redirection_host_service.delete_redirection_host(db, host_id)
    except redirection_host_service.RedirectionHostNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Redirection host not found"
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.delete,
        object_type="redirection_host",
        object_id=host_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=response.headers)


__all__ = ["router"]
