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

import asyncio
import time

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import AdminUser, SessionDep
from app.api.routes._config_writes import after_config_write
from app.api.routes.crowdsec import RELOADS_NOT_CONFIGURED, enqueue_control_task
from app.models.enums import AuditAction
from app.schemas.instance_settings import (
    CrowdSecBanUpdate,
    CrowdSecCapiUpdate,
    CrowdSecHubUpdate,
    InstanceSettingsRead,
    InstanceSettingsUpdate,
    LlmSettingsUpdate,
    LlmTestRequest,
    LlmTestResult,
    MailTestRequest,
    MailTestResult,
    SmtpSettingsUpdate,
)
from app.services import instance_settings as settings_service
from app.services.audit import record_audit
from app.services.llm import LlmConfig, check_connection
from app.services.mail import sender as mail_sender
from app.services.mail.config import MailNotConfigured
from app.services.mail.templates import APP_NAME
from app.services.mail.templates import render as render_email

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


@router.patch("/smtp", response_model=InstanceSettingsRead)
async def update_smtp_settings(
    body: SmtpSettingsUpdate, admin: AdminUser, db: SessionDep
) -> InstanceSettingsRead:
    """Configure outbound email. Admin-only.

    ``exclude_unset`` is load-bearing: it is what tells the service the
    difference between "the client did not send a password" and "the client
    cleared the password".
    """
    changes = body.model_dump(exclude_unset=True)
    row = await settings_service.update_smtp_settings(db, changes)
    await record_audit(
        db,
        actor=admin.email,
        action=AuditAction.update,
        object_type="instance_settings",
        object_id=row.id,
        # Field names and non-secret values only — never the password.
        meta={
            "smtp_enabled": row.smtp_enabled,
            "smtp_host": row.smtp_host,
            "smtp_password_changed": "smtp_password" in changes,
        },
    )
    await db.commit()
    return InstanceSettingsRead.from_row(row)


@router.post("/smtp/test", response_model=MailTestResult)
async def send_test_email(
    body: MailTestRequest, admin: AdminUser, db: SessionDep
) -> MailTestResult:
    """Send one themed test message. Admin-only.

    Synchronous on purpose. The operator is on the Settings page waiting and
    needs the actual SMTP error — "authentication failed", "connection refused"
    — not a task id to go and poll. Real notifications will go through Celery
    instead, so a slow mail server never fails a user-facing action.

    A failed send returns **200 with ``ok: false``**, not a 4xx or 5xx: the API
    call succeeded, the mail server did not. An error status would make a
    working endpoint indistinguishable from a broken one in monitoring.
    """
    row = await settings_service.get_instance_settings(db)
    config = settings_service.mail_config_from_row(row)
    recipient = body.to or admin.email
    email = render_email("test_email", subject=f"{APP_NAME} test email", app_name=APP_NAME)

    started = time.perf_counter()
    try:
        # to_thread because smtplib blocks: without it a slow mail server would
        # stall the whole event loop, not just this request.
        await asyncio.to_thread(mail_sender.send_email, config, to=recipient, email=email)
    except MailNotConfigured as exc:
        return MailTestResult(ok=False, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - any SMTP failure is a result here
        return MailTestResult(
            ok=False,
            detail=f"{type(exc).__name__}: {exc}",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    return MailTestResult(
        ok=True,
        detail=f"Sent to {recipient}.",
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


# --- CrowdSec maintenance (Security → Updates) ----------------------------------------


@router.patch("/crowdsec-hub", response_model=InstanceSettingsRead)
async def update_crowdsec_hub_settings(
    body: CrowdSecHubUpdate, admin: AdminUser, db: SessionDep
) -> InstanceSettingsRead:
    """The hub refresh schedule. Admin-only. Takes effect at the next hourly tick."""
    row = await settings_service.update_crowdsec_hub(db, body.model_dump())
    await record_audit(
        db,
        actor=admin.email,
        action=AuditAction.update,
        object_type="instance_settings",
        object_id=row.id,
        meta={"crowdsec_hub": body.model_dump(mode="json")},
    )
    await db.commit()
    return InstanceSettingsRead.from_row(row)


@router.patch(
    "/crowdsec-capi", response_model=InstanceSettingsRead, status_code=status.HTTP_202_ACCEPTED
)
async def update_crowdsec_capi_settings(
    body: CrowdSecCapiUpdate, admin: AdminUser, db: SessionDep
) -> InstanceSettingsRead:
    """Desired state of the community blocklist; enqueues the apply. Admin-only.

    Saved even when the apply cannot be enqueued, so the choice is not lost;
    the 409 tells the operator why nothing happened.
    """
    row = await settings_service.update_crowdsec_capi(db, enabled=body.enabled)
    await record_audit(
        db,
        actor=admin.email,
        action=AuditAction.enable if body.enabled else AuditAction.disable,
        object_type="instance_settings",
        object_id=row.id,
        meta={"crowdsec_capi_enabled": body.enabled},
    )
    await db.commit()
    if not enqueue_control_task("app.tasks.crowdsec.apply_capi"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=RELOADS_NOT_CONFIGURED)
    return InstanceSettingsRead.from_row(row)


__all__ = ["router"]
