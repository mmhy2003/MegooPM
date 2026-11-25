"""Static-site CRUD routes (admin-only).

A static site claims a set of domains and serves the files of one folder under
the read-only sites mount (``settings.sites_root``). This router exposes full
CRUD plus ``/folders`` for the folder picker; every mutating write records an
audit entry and enqueues an nginx regenerate-and-reload (see
:mod:`app.api.routes._config_writes`), returning the reload task id in the
``X-Config-Reload-Task`` header. A create/update referencing a non-existent
certificate or access list is rejected with 422 before any config is generated.

Every site carries ``folder_status``: what the backend found at its folder. It
is advisory: a site whose files are not uploaded yet saves fine and serves 404
until they are.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import AdminUser, SessionDep
from app.api.routes._config_writes import after_config_write
from app.core.config import settings
from app.models.enums import AuditAction
from app.models.static_site import StaticSite
from app.schemas.static_site import (
    FoldersResult,
    StaticSiteCreate,
    StaticSiteRead,
    StaticSiteUpdate,
)
from app.services import static_site as static_site_service

router = APIRouter(tags=["static-sites"])

_NOT_FOUND = "Static site not found"


def _statuses(folders: list[str]) -> list[str]:
    root = Path(settings.sites_root)
    return [static_site_service.folder_status(root, folder) for folder in folders]


async def _read_all(sites: list[StaticSite]) -> list[StaticSiteRead]:
    # The checks stat the sites mount, which under HA is a shared NFS export:
    # off the event loop, so a stalled mount stalls this request, not the node.
    statuses = await asyncio.to_thread(_statuses, [s.folder for s in sites])
    out = [StaticSiteRead.model_validate(s) for s in sites]
    for read, folder_status in zip(out, statuses, strict=True):
        read.folder_status = folder_status
    return out


async def _read(site: StaticSite) -> StaticSiteRead:
    return (await _read_all([site]))[0]


@router.get("", response_model=list[StaticSiteRead])
async def list_static_sites(_user: AdminUser, db: SessionDep) -> list[StaticSiteRead]:
    """List all static sites."""
    sites = await static_site_service.list_static_sites(db)
    return await _read_all(list(sites))


@router.get("/folders", response_model=FoldersResult)
async def list_folders(_user: AdminUser) -> FoldersResult:
    """The folders under the sites mount a site can serve, two levels down."""
    return await asyncio.to_thread(static_site_service.list_folders, Path(settings.sites_root))


@router.post("", response_model=StaticSiteRead, status_code=status.HTTP_201_CREATED)
async def create_static_site(
    body: StaticSiteCreate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> StaticSiteRead:
    """Create a static site, then regenerate and reload nginx."""
    try:
        site = await static_site_service.create_static_site(db, body.model_dump())
    except static_site_service.InvalidReferenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.create,
        object_type="static_site",
        object_id=site.id,
        meta={"domain_names": site.domain_names, "folder": site.folder},
    )
    return await _read(site)


@router.get("/{site_id}", response_model=StaticSiteRead)
async def get_static_site(site_id: int, _user: AdminUser, db: SessionDep) -> StaticSiteRead:
    """Return one static site."""
    site = await static_site_service.get_static_site(db, site_id)
    if site is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return await _read(site)


@router.patch("/{site_id}", response_model=StaticSiteRead)
async def update_static_site(
    site_id: int,
    body: StaticSiteUpdate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> StaticSiteRead:
    """Partially update a static site, then regenerate and reload nginx."""
    changes = body.model_dump(exclude_unset=True)
    try:
        site = await static_site_service.update_static_site(db, site_id, changes)
    except static_site_service.StaticSiteNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND) from None
    except static_site_service.InvalidReferenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.update,
        object_type="static_site",
        object_id=site.id,
        meta={"changed": sorted(changes)},
    )
    return await _read(site)


@router.delete("/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_static_site(
    site_id: int,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> Response:
    """Delete a static site, then regenerate and reload nginx."""
    try:
        await static_site_service.delete_static_site(db, site_id)
    except static_site_service.StaticSiteNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.delete,
        object_type="static_site",
        object_id=site_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=response.headers)


__all__ = ["router"]
