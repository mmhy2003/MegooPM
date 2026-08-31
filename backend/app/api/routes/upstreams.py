"""Upstream-pool CRUD routes (admin-only).

A pool is a load-balanced set of backend servers a proxy host forwards to. This
router exposes full CRUD over pools plus a ``/backends`` sub-resource for
managing individual servers. Every mutating write records an audit entry and
enqueues an nginx regenerate-and-reload (see
:mod:`app.api.routes._config_writes`); the reload task id is returned in the
``X-Config-Reload-Task`` response header.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import AdminUser, SessionDep
from app.api.routes._config_writes import after_config_write
from app.models.enums import AuditAction
from app.schemas.upstream import (
    BackendCreate,
    BackendRead,
    BackendUpdate,
    UpstreamCreate,
    UpstreamRead,
    UpstreamUpdate,
)
from app.services import upstream as upstream_service

router = APIRouter(tags=["upstreams"])


@router.get("", response_model=list[UpstreamRead])
async def list_upstreams(_admin: AdminUser, db: SessionDep) -> list[UpstreamRead]:
    """List all upstream pools with their backends. Admin-only."""
    pools = await upstream_service.list_upstreams(db)
    return [UpstreamRead.model_validate(p) for p in pools]


@router.post("", response_model=UpstreamRead, status_code=status.HTTP_201_CREATED)
async def create_upstream(
    body: UpstreamCreate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> UpstreamRead:
    """Create an upstream pool, optionally seeding backends inline. Admin-only."""
    try:
        pool = await upstream_service.create_upstream(
            db,
            name=body.name,
            description=body.description,
            lb_method=body.lb_method,
            context=body.context,
            enabled=body.enabled,
            backends=[b.model_dump() for b in body.backends],
        )
    except upstream_service.InvalidPoolConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    except upstream_service.DuplicateBackendError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Duplicate backend (host, port) within the pool",
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.create,
        object_type="upstream",
        object_id=pool.id,
        meta={"name": pool.name, "backends": len(pool.backends)},
    )
    return UpstreamRead.model_validate(pool)


@router.get("/{upstream_id}", response_model=UpstreamRead)
async def get_upstream(upstream_id: int, _admin: AdminUser, db: SessionDep) -> UpstreamRead:
    """Fetch a single upstream pool. Admin-only."""
    pool = await upstream_service.get_upstream(db, upstream_id)
    if pool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upstream not found")
    return UpstreamRead.model_validate(pool)


@router.patch("/{upstream_id}", response_model=UpstreamRead)
async def update_upstream(
    upstream_id: int,
    body: UpstreamUpdate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> UpstreamRead:
    """Update a pool's own attributes (name/description/lb_method/enabled)."""
    changes = body.model_dump(exclude_unset=True)
    try:
        pool = await upstream_service.update_upstream(db, upstream_id, changes)
    except upstream_service.InvalidPoolConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    except upstream_service.UpstreamNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Upstream not found"
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.update,
        object_type="upstream",
        object_id=pool.id,
        meta={"changed": sorted(changes)},
    )
    return UpstreamRead.model_validate(pool)


@router.delete("/{upstream_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_upstream(
    upstream_id: int,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> Response:
    """Delete a pool (and its backends). 409 if still referenced by a host."""
    try:
        await upstream_service.delete_upstream(db, upstream_id)
    except upstream_service.UpstreamNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Upstream not found"
        ) from None
    except upstream_service.UpstreamInUseError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Upstream is still referenced by one or more proxy hosts",
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.delete,
        object_type="upstream",
        object_id=upstream_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=response.headers)


# --- Backend sub-resource --------------------------------------------------


@router.post(
    "/{upstream_id}/backends",
    response_model=BackendRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_backend(
    upstream_id: int,
    body: BackendCreate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> BackendRead:
    """Add a backend server to a pool. Admin-only."""
    try:
        backend = await upstream_service.add_backend(db, upstream_id, body.model_dump())
    except upstream_service.InvalidPoolConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    except upstream_service.UpstreamNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Upstream not found"
        ) from None
    except upstream_service.DuplicateBackendError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A backend with that host and port already exists in the pool",
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.update,
        object_type="upstream",
        object_id=upstream_id,
        meta={"added_backend": f"{backend.host}:{backend.port}"},
    )
    return BackendRead.model_validate(backend)


@router.patch("/{upstream_id}/backends/{backend_id}", response_model=BackendRead)
async def update_backend(
    upstream_id: int,
    backend_id: int,
    body: BackendUpdate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> BackendRead:
    """Update a backend within a pool. Admin-only."""
    changes = body.model_dump(exclude_unset=True)
    try:
        backend = await upstream_service.update_backend(db, upstream_id, backend_id, changes)
    except upstream_service.InvalidPoolConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    except upstream_service.BackendNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Backend not found in this pool"
        ) from None
    except upstream_service.DuplicateBackendError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A backend with that host and port already exists in the pool",
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.update,
        object_type="upstream",
        object_id=upstream_id,
        meta={"updated_backend": backend_id, "changed": sorted(changes)},
    )
    return BackendRead.model_validate(backend)


@router.delete(
    "/{upstream_id}/backends/{backend_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_backend(
    upstream_id: int,
    backend_id: int,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> Response:
    """Remove a backend from a pool. Admin-only."""
    try:
        await upstream_service.remove_backend(db, upstream_id, backend_id)
    except upstream_service.BackendNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Backend not found in this pool"
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.update,
        object_type="upstream",
        object_id=upstream_id,
        meta={"removed_backend": backend_id},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=response.headers)


__all__ = ["router"]
