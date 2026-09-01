"""Instance-settings routes (admin-only).

There is one settings row, so no path carries an id — but settings are grouped,
and each group gets its own ``PATCH``. A single patch over the whole row cannot
work: each group has a coherence rule ("redirect needs a URL", "enabled needs a
model") that can only be checked against a payload carrying that group's
discriminator, so one combined route would force resending every group to change
any one of them.

Only the default-site group renders into nginx, so only its write goes through
:func:`~app.api.routes._config_writes.after_config_write` — audited *and*
followed by a regenerate-and-reload, with the task id in
``X-Config-Reload-Task``. The LLM group is audited with
:func:`~app.services.audit.record_audit` and enqueues no reload.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import AdminUser, SessionDep
from app.api.routes._config_writes import after_config_write
from app.models.enums import AuditAction
from app.schemas.instance_settings import (
    InstanceSettingsRead,
    InstanceSettingsUpdate,
    LlmSettingsUpdate,
)
from app.services import instance_settings as settings_service
from app.services.audit import record_audit

router = APIRouter(tags=["settings"])


@router.get("", response_model=InstanceSettingsRead)
async def read_settings(_admin: AdminUser, db: SessionDep) -> InstanceSettingsRead:
    """Read the instance settings. Admin-only."""
    row = await settings_service.get_instance_settings(db)
    return InstanceSettingsRead.from_row(row)


@router.patch("/default-site", response_model=InstanceSettingsRead)
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
    return InstanceSettingsRead.from_row(row)


@router.patch("/llm", response_model=InstanceSettingsRead)
async def update_llm_settings(
    body: LlmSettingsUpdate, admin: AdminUser, db: SessionDep
) -> InstanceSettingsRead:
    """Configure the LLM integration. Admin-only.

    ``exclude_unset`` is load-bearing: it is what tells the service the
    difference between "the client did not send a key" and "the client cleared
    the key".
    """
    changes = body.model_dump(exclude_unset=True)
    row = await settings_service.update_llm_settings(db, changes)
    await record_audit(
        db,
        actor=admin.email,
        action=AuditAction.update,
        object_type="instance_settings",
        object_id=row.id,
        # The field name, never the value — and never the ciphertext.
        meta={
            "llm_enabled": row.llm_enabled,
            "llm_model": row.llm_model,
            "llm_api_key_changed": "llm_api_key" in changes,
        },
    )
    await db.commit()
    return InstanceSettingsRead.from_row(row)


__all__ = ["router"]
