"""Instance-settings routes (admin-only).

There is one settings row, so the path carries no id — ``/settings``, not
``/settings/{id}``. Writes change rendered nginx configuration, so they go
through :func:`~app.api.routes._config_writes.after_config_write`: audited *and*
followed by a regenerate-and-reload, with the task id in
``X-Config-Reload-Task``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import AdminUser, SessionDep
from app.api.routes._config_writes import after_config_write
from app.models.enums import AuditAction
from app.schemas.instance_settings import InstanceSettingsRead, InstanceSettingsUpdate
from app.services import instance_settings as settings_service

router = APIRouter(tags=["settings"])


@router.get("", response_model=InstanceSettingsRead)
async def read_settings(_admin: AdminUser, db: SessionDep) -> InstanceSettingsRead:
    """Read the instance settings. Admin-only."""
    row = await settings_service.get_instance_settings(db)
    return InstanceSettingsRead.model_validate(row)


@router.patch("", response_model=InstanceSettingsRead)
async def update_settings(
    body: InstanceSettingsUpdate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> InstanceSettingsRead:
    """Set the default site. Admin-only.

    ``default_site_mode`` is required; the columns the chosen mode does not use
    are cleared, so the stored row always describes exactly one configuration.
    """
    changes = body.model_dump()
    try:
        row = await settings_service.update_default_site(db, changes)
    except settings_service.UnknownCustomPageError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="default_site_page_id does not reference an existing custom page",
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.update,
        object_type="instance_settings",
        object_id=row.id,
        meta={"default_site_mode": row.default_site_mode.value},
    )
    return InstanceSettingsRead.model_validate(row)


__all__ = ["router"]
