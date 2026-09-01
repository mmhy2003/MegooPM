"""Custom-page CRUD routes (admin-only).

A custom page is a named, self-contained HTML document authored in the app —
images embedded as base64 ``data:`` URIs, so there are no side-car assets.

Most writes here do **not** enqueue an nginx reload: a page nothing references
changes no rendered configuration, so they are audited via
:func:`~app.services.audit.record_audit` directly. The exception is the page the
default site points at — editing that one must converge, or the change would sit
in the database until an unrelated edit happened to trigger a reload, at which
point it would appear with no apparent cause. Deleting that page is refused
outright by the ``RESTRICT`` foreign key, so a delete that succeeds never needs
a reload.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError

from app.api.deps import AdminUser, SessionDep
from app.api.routes._config_writes import after_config_write
from app.models.enums import AuditAction
from app.models.instance_settings import InstanceSettings
from app.schemas.custom_page import (
    CustomPageCreate,
    CustomPageRead,
    CustomPageSummary,
    CustomPageUpdate,
    PageAssistRequest,
    PageAssistResponse,
    PageEditChange,
)
from app.services import custom_page as custom_page_service
from app.services import instance_settings as settings_service
from app.services.audit import record_audit
from app.services.llm import LlmError, LlmNotConfiguredError
from app.services.page_assist import assist_page

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


async def _is_default_site(db: SessionDep, page_id: int) -> bool:
    """Whether the default site currently serves this page."""
    row = await db.get(InstanceSettings, 1)
    return row is not None and row.default_site_page_id == page_id


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


@router.post("/assist", response_model=PageAssistResponse)
async def assist_custom_page(
    body: PageAssistRequest, admin: AdminUser, db: SessionDep
) -> PageAssistResponse:
    """Write or revise a page with the configured model. Admin-only.

    Stateless: it takes the document rather than a page id, so it works on a
    page that has never been saved.

    ``html`` arrives already elided and the response is re-hydrated in the
    browser, which is why nothing here knows about images.

    A provider failure is **502**, not 422: the request was well-formed and the
    client can do nothing about it, so blurring it into the 4xx that mean
    "you sent something invalid" would lose that distinction in the logs. This
    is the opposite of the settings probe, which reports a provider failure as
    200 with ``ok: false`` — there, reporting on the connection *is* the job.
    """
    row = await settings_service.get_instance_settings(db)
    if not row.llm_enabled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Enable LLM features in Settings before using AI editing",
        )

    config = settings_service.llm_config_from_row(row)
    try:
        result = await assist_page(config, instruction=body.instruction, html=body.html)
    except LlmNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    except LlmError as exc:
        # Already scrubbed of credentials by app/services/llm.py.
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from None

    await _audit(
        db,
        actor=admin.email,
        action=AuditAction.update,
        page_id=None,
        # The instruction and a size — never the document, which is the
        # operator's content and can be megabytes.
        instruction=body.instruction[:200],
        result_bytes=len(result.html.encode("utf-8")),
        mode=result.mode,
        edits=len(result.changes),
    )
    return PageAssistResponse(
        html=result.html,
        mode=result.mode,
        truncated=result.truncated,
        changes=[
            PageEditChange(start=c.start, end=c.end, before=c.before, after=c.after)
            for c in result.changes
        ],
    )


@router.get("/{page_id}", response_model=CustomPageRead)
async def get_custom_page(page_id: int, _admin: AdminUser, db: SessionDep) -> CustomPageRead:
    """Fetch one page including its document. Admin-only."""
    page = await custom_page_service.get_custom_page(db, page_id)
    if page is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom page not found")
    return CustomPageRead.model_validate(page)


@router.patch("/{page_id}", response_model=CustomPageRead)
async def update_custom_page(
    page_id: int,
    body: CustomPageUpdate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
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
    if await _is_default_site(db, page.id):
        # This page is being served right now; converge it.
        await after_config_write(
            db,
            response,
            actor=admin,
            action=AuditAction.update,
            object_type="custom_page",
            object_id=page.id,
            meta={"changed": sorted(changes), "default_site": True},
        )
    else:
        await _audit(
            db,
            actor=admin.email,
            action=AuditAction.update,
            page_id=page.id,
            changed=sorted(changes),
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
    except IntegrityError:
        # The only FK to custom_pages is instance_settings.default_site_page_id,
        # declared RESTRICT precisely so this delete fails instead of silently
        # changing what every unmatched visitor sees. The rollback matters: a
        # poisoned session would fail the audit write that follows too.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This page is in use by the Default site.",
        ) from None
    await _audit(db, actor=admin.email, action=AuditAction.delete, page_id=page_id)


__all__ = ["router"]
