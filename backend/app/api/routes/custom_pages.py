"""Custom-page CRUD routes (admin-only).

A custom page is a named, self-contained HTML document authored in the app —
images embedded as base64 ``data:`` URIs, so there are no side-car assets.

Unlike every other resource in this API, these writes do **not** enqueue an
nginx reload: nothing in the rendered configuration references a page yet, so
there is nothing to converge and no ``X-Config-Reload-Task`` header to return.
They are still audited, via :func:`~app.services.audit.record_audit` directly
rather than :func:`~app.api.routes._config_writes.after_config_write`. When a
binding does land (a CrowdSec ban page, a catch-all for unmatched hosts), this
router switches to the shared helper like the others.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import AdminUser, SessionDep
from app.models.enums import AuditAction
from app.schemas.custom_page import (
    CustomPageCreate,
    CustomPageRead,
    CustomPageSummary,
    CustomPageUpdate,
)
from app.services import custom_page as custom_page_service
from app.services.audit import record_audit

router = APIRouter(tags=["custom-pages"])

_DUPLICATE_NAME = "A custom page with that name already exists"


async def _audit(
    db: SessionDep, *, actor: str, action: AuditAction, page_id: int | None, **meta: object
) -> None:
    """Record the write. No reload: no rendered config references a page yet."""
    await record_audit(
        db,
        actor=actor,
        action=action,
        object_type="custom_page",
        object_id=page_id,
        meta=dict(meta),
    )
    await db.commit()


@router.get("", response_model=list[CustomPageSummary])
async def list_custom_pages(_admin: AdminUser, db: SessionDep) -> list[CustomPageSummary]:
    """List every page, without their documents. Admin-only."""
    pages = await custom_page_service.list_custom_pages(db)
    return [CustomPageSummary.from_page(p) for p in pages]


@router.post("", response_model=CustomPageRead, status_code=status.HTTP_201_CREATED)
async def create_custom_page(
    body: CustomPageCreate, admin: AdminUser, db: SessionDep
) -> CustomPageRead:
    """Create a page. Admin-only."""
    try:
        page = await custom_page_service.create_custom_page(db, body.model_dump())
    except custom_page_service.DuplicateNameError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_DUPLICATE_NAME) from None
    await _audit(db, actor=admin.email, action=AuditAction.create, page_id=page.id, name=page.name)
    return CustomPageRead.model_validate(page)


@router.get("/{page_id}", response_model=CustomPageRead)
async def get_custom_page(page_id: int, _admin: AdminUser, db: SessionDep) -> CustomPageRead:
    """Fetch one page including its document. Admin-only."""
    page = await custom_page_service.get_custom_page(db, page_id)
    if page is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom page not found")
    return CustomPageRead.model_validate(page)


@router.patch("/{page_id}", response_model=CustomPageRead)
async def update_custom_page(
    page_id: int, body: CustomPageUpdate, admin: AdminUser, db: SessionDep
) -> CustomPageRead:
    """Update a page's name, description and/or document. Admin-only."""
    changes = body.model_dump(exclude_unset=True)
    try:
        page = await custom_page_service.update_custom_page(db, page_id, changes)
    except custom_page_service.CustomPageNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Custom page not found"
        ) from None
    except custom_page_service.DuplicateNameError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_DUPLICATE_NAME) from None
    await _audit(
        db, actor=admin.email, action=AuditAction.update, page_id=page.id, changed=sorted(changes)
    )
    return CustomPageRead.model_validate(page)


@router.delete("/{page_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_custom_page(page_id: int, admin: AdminUser, db: SessionDep) -> None:
    """Delete a page. Admin-only."""
    try:
        await custom_page_service.delete_custom_page(db, page_id)
    except custom_page_service.CustomPageNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Custom page not found"
        ) from None
    await _audit(db, actor=admin.email, action=AuditAction.delete, page_id=page_id)


__all__ = ["router"]
