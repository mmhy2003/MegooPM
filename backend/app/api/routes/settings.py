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

``app.services.llm`` is imported here at module scope, which is safe: that
module does not import ``litellm`` until one of its functions runs, so nothing
here pays the 3.49s the package costs to load.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import AdminUser, SessionDep
from app.api.routes._config_writes import after_config_write
from app.models.enums import AuditAction
from app.schemas.instance_settings import (
    CrowdSecBanUpdate,
    InstanceSettingsRead,
    InstanceSettingsUpdate,
    LlmSettingsUpdate,
    LlmTestRequest,
    LlmTestResult,
)
from app.services import instance_settings as settings_service
from app.services.audit import record_audit
from app.services.llm import LlmConfig, check_connection

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


@router.patch("/ban-page", response_model=InstanceSettingsRead)
async def update_ban_page_settings(
    body: CrowdSecBanUpdate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> InstanceSettingsRead:
    """Choose what a CrowdSec-blocked visitor is served. Admin-only.

    ``after_config_write``, not a bare audit: this changes a file nginx serves,
    so the config has to be rewritten and reloaded for the choice to take
    effect at all.
    """
    changes = body.model_dump()
    try:
        row = await settings_service.update_ban_page(db, changes)
    except settings_service.UnknownCustomPageError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="crowdsec_ban_page_id does not reference an existing custom page",
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.update,
        object_type="instance_settings",
        object_id=row.id,
        meta={"crowdsec_ban_mode": row.crowdsec_ban_mode.value},
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


@router.post("/llm/test", response_model=LlmTestResult)
async def test_llm_connection(
    body: LlmTestRequest, _admin: AdminUser, db: SessionDep
) -> LlmTestResult:
    """Probe the LLM configuration end to end. Admin-only.

    Overrides in the body win over the stored row, so a key can be checked
    before it is saved.

    This deliberately ignores ``llm_enabled``. That flag stops *feature* code
    running when the operator has switched the integration off; requiring it
    here would invert the order an operator actually works in — configure,
    prove it works, then enable.

    A failed probe returns **200 with ``ok: false``**, not a 4xx or 5xx: the API
    call succeeded, the upstream did not. An error status would make a working
    endpoint indistinguishable from a broken one in logs and monitoring.
    """
    row = await settings_service.get_instance_settings(db)
    stored = settings_service.llm_config_from_row(row)
    config = LlmConfig(
        model=body.model or stored.model,
        api_key=body.api_key or stored.api_key,
        api_base=body.api_base or stored.api_base,
    )
    if not config.model:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Set a model before testing the connection",
        )
    result = await check_connection(config)
    return LlmTestResult(
        ok=result.ok,
        model=result.model,
        reply=result.reply,
        error=result.error,
        latency_ms=result.latency_ms,
    )


__all__ = ["router"]
