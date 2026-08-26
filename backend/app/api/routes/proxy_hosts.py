"""Proxy-host CRUD routes (admin-only).

A proxy host terminates domain names and forwards to an upstream pool. This
router exposes full CRUD; every mutating write records an audit entry and
enqueues an nginx regenerate-and-reload (see
:mod:`app.api.routes._config_writes`), returning the reload task id in the
``X-Config-Reload-Task`` header. A create/update that references a non-existent
pool is rejected with 422 before any config is generated.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import AdminUser, SessionDep
from app.api.routes._config_writes import after_config_write
from app.models.enums import AuditAction
from app.schemas.proxy_host import ProxyHostCreate, ProxyHostRead, ProxyHostUpdate
from app.services import proxy_host as proxy_host_service

router = APIRouter(tags=["proxy-hosts"])


@router.get("", response_model=list[ProxyHostRead])
async def list_proxy_hosts(_admin: AdminUser, db: SessionDep) -> list[ProxyHostRead]:
    """List all proxy hosts. Admin-only."""
    hosts = await proxy_host_service.list_proxy_hosts(db)
    return [ProxyHostRead.model_validate(h) for h in hosts]


@router.post("", response_model=ProxyHostRead, status_code=status.HTTP_201_CREATED)
async def create_proxy_host(
    body: ProxyHostCreate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> ProxyHostRead:
    """Create a proxy host forwarding to an upstream pool. Admin-only."""
    try:
        host = await proxy_host_service.create_proxy_host(db, body.model_dump())
    except proxy_host_service.InvalidReferenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.create,
        object_type="proxy_host",
        object_id=host.id,
        meta={"domain_names": host.domain_names, "upstream_id": host.upstream_id},
    )
    return ProxyHostRead.model_validate(host)


@router.get("/{host_id}", response_model=ProxyHostRead)
async def get_proxy_host(host_id: int, _admin: AdminUser, db: SessionDep) -> ProxyHostRead:
    """Fetch a single proxy host. Admin-only."""
    host = await proxy_host_service.get_proxy_host(db, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proxy host not found")
    return ProxyHostRead.model_validate(host)


@router.patch("/{host_id}", response_model=ProxyHostRead)
async def update_proxy_host(
    host_id: int,
    body: ProxyHostUpdate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> ProxyHostRead:
    """Update a proxy host. Admin-only."""
    changes = body.model_dump(exclude_unset=True)
    try:
        host = await proxy_host_service.update_proxy_host(db, host_id, changes)
    except proxy_host_service.ProxyHostNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Proxy host not found"
        ) from None
    except proxy_host_service.InvalidReferenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.update,
        object_type="proxy_host",
        object_id=host.id,
        meta={"changed": sorted(changes)},
    )
    return ProxyHostRead.model_validate(host)


@router.delete("/{host_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_proxy_host(
    host_id: int,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> Response:
    """Delete a proxy host. Admin-only."""
    try:
        await proxy_host_service.delete_proxy_host(db, host_id)
    except proxy_host_service.ProxyHostNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Proxy host not found"
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.delete,
        object_type="proxy_host",
        object_id=host_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=response.headers)


__all__ = ["router"]
