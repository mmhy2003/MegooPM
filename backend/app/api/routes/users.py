"""User management routes.

Admin-only: list, create, update, reset password, delete. Any signed-in user:
``GET /users/me`` (self-service ``PATCH /users/me`` and ``PUT
/users/me/password`` are added alongside). Lock-out rules live in the service
layer and surface here as **409**; every mutation writes an audit row.

Route order matters: the ``/me`` routes are declared before ``/{user_id}`` so
they can never be captured by the integer path parameter.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.models.enums import AuditAction, AuthTokenKind
from app.models.user import User
from app.schemas.user import (
    PasswordChange,
    PasswordReset,
    ProfileUpdate,
    TotpCodeRequest,
    TotpCodes,
    TotpSetup,
    UserCreate,
    UserInvite,
    UserRead,
    UserUpdate,
)
from app.services import auth_tokens, totp
from app.services import instance_settings as settings_service
from app.services import user as user_service
from app.services.audit import record_audit
from app.services.mail.templates import APP_NAME
from app.tasks.mail import send_email as send_email_task

router = APIRouter(tags=["users"])


# --- helpers ------------------------------------------------------------------


async def _get_or_404(db: AsyncSession, user_id: int) -> User:
    user = await user_service.get_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


async def _audit(
    db: AsyncSession,
    *,
    actor: User,
    action: AuditAction,
    object_id: int | None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Record one ``user`` audit row and commit it (same short-transaction
    pattern as ``_config_writes.after_config_write``)."""
    await record_audit(
        db,
        actor=actor.email,
        action=action,
        object_type="user",
        object_id=object_id,
        meta=meta,
    )
    await db.commit()


def _action_for(changes: dict[str, list[object]]) -> AuditAction:
    """A lone ``is_active`` flip is an enable/disable; anything else is an update."""
    if set(changes) == {"is_active"}:
        return AuditAction.enable if changes["is_active"][1] else AuditAction.disable
    return AuditAction.update


def _conflict(exc: user_service.UserProtectionError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


_MAIL_NOT_CONFIGURED = (
    "Email is not configured, so an invitation cannot be sent. "
    "Set up SMTP and the app URL in Settings first."
)


async def _send_invitation(db: AsyncSession, user: User, *, inviter: User) -> None:
    """Issue a fresh invitation token and queue the email. One place, so the
    initial invite and a resend can never drift apart."""
    row = await settings_service.get_instance_settings(db)
    if not (row.smtp_enabled and row.app_url):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_MAIL_NOT_CONFIGURED)
    raw = await auth_tokens.issue(
        db, user=user, kind=AuthTokenKind.invitation, ttl=auth_tokens.INVITE_TTL
    )
    send_email_task.delay(
        to=user.email,
        template="invitation",
        subject=f"You're invited to {APP_NAME}",
        context={
            "app_name": APP_NAME,
            "inviter_name": inviter.full_name.strip() or inviter.email,
            "accept_url": f"{row.app_url}/accept-invite?token={raw}",
            "ttl_days": auth_tokens.INVITE_TTL.days,
        },
    )


# --- self ---------------------------------------------------------------------


@router.get("/me", response_model=UserRead)
async def read_current_user(current_user: CurrentUser) -> UserRead:
    """Return the authenticated caller."""
    return UserRead.model_validate(current_user)


@router.patch("/me", response_model=UserRead)
async def update_current_user(
    body: ProfileUpdate,
    current_user: CurrentUser,
    db: SessionDep,
) -> UserRead:
    """Edit the caller's own display name."""
    user, changes = await user_service.update_user(
        db, current_user, actor=current_user, full_name=body.full_name
    )
    if changes:
        await _audit(
            db,
            actor=current_user,
            action=AuditAction.update,
            object_id=user.id,
            meta={"changes": changes},
        )
    return UserRead.model_validate(user)


@router.put("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_current_user_password(
    body: PasswordChange,
    current_user: CurrentUser,
    db: SessionDep,
) -> None:
    """Change the caller's own password (no current-password check; the session is the proof)."""
    await user_service.change_own_password(db, current_user, new_password=body.new_password)
    await _audit(
        db,
        actor=current_user,
        action=AuditAction.update,
        object_id=current_user.id,
        meta={"password_changed": True},
    )


def _invalid_code() -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=totp.INVALID_CODE_MESSAGE)


def _totp_off() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail="Two-factor authentication is not on."
    )


@router.post("/me/totp/setup", response_model=TotpSetup)
async def totp_setup(current_user: CurrentUser, db: SessionDep) -> TotpSetup:
    """Start enrolling an authenticator app. 2FA stays off until confirmed."""
    try:
        secret = await totp.start_enrolment(db, current_user)
    except totp.TotpAlreadyEnabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Two-factor authentication is already on. Turn it off first.",
        ) from None
    return TotpSetup(secret=secret, otpauth_uri=totp.provisioning_uri(secret, current_user.email))


@router.post("/me/totp/enable", response_model=TotpCodes)
async def totp_enable(
    body: TotpCodeRequest, current_user: CurrentUser, db: SessionDep
) -> TotpCodes:
    """Prove the app works, then turn 2FA on. Returns the recovery codes once."""
    try:
        codes = await totp.confirm_enrolment(db, current_user, body.code)
    except totp.TotpNotPending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Start setup before confirming it."
        ) from None
    if codes is None:
        raise _invalid_code()
    await _audit(
        db,
        actor=current_user,
        action=AuditAction.update,
        object_id=current_user.id,
        meta={"totp": "enabled"},
    )
    return TotpCodes(codes=codes)


@router.post("/me/totp/disable", status_code=status.HTTP_204_NO_CONTENT)
async def totp_disable(body: TotpCodeRequest, current_user: CurrentUser, db: SessionDep) -> None:
    """Turn 2FA off. A valid code is required: a stolen session must not be
    able to strip the second factor."""
    if not current_user.totp_enabled:
        raise _totp_off()
    if not await totp.verify_code(db, current_user, body.code):
        raise _invalid_code()
    await totp.disable(db, current_user)
    await _audit(
        db,
        actor=current_user,
        action=AuditAction.update,
        object_id=current_user.id,
        meta={"totp": "disabled"},
    )


@router.post("/me/totp/recovery-codes", response_model=TotpCodes)
async def totp_regenerate(
    body: TotpCodeRequest, current_user: CurrentUser, db: SessionDep
) -> TotpCodes:
    """Replace every recovery code. A valid code is required."""
    if not current_user.totp_enabled:
        raise _totp_off()
    if not await totp.verify_code(db, current_user, body.code):
        raise _invalid_code()
    codes = await totp.mint_recovery_codes(db, current_user)
    await _audit(
        db,
        actor=current_user,
        action=AuditAction.update,
        object_id=current_user.id,
        meta={"totp": "codes_regenerated"},
    )
    return TotpCodes(codes=codes)


# --- admin: collection --------------------------------------------------------


@router.get("", response_model=list[UserRead])
async def list_users(
    _admin: AdminUser,
    db: SessionDep,
) -> list[UserRead]:
    """List all users. Admin-only."""
    users = await user_service.list_users(db)
    return [UserRead.model_validate(u) for u in users]


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    admin: AdminUser,
    db: SessionDep,
) -> UserRead:
    """Create a user with an explicit role. Admin-only."""
    try:
        user = await user_service.create_user(
            db,
            email=body.email,
            password=body.password,
            full_name=body.full_name,
            role=body.role,
            is_active=body.is_active,
        )
    except user_service.EmailAlreadyExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with that email already exists",
        ) from None
    await _audit(
        db,
        actor=admin,
        action=AuditAction.create,
        object_id=user.id,
        meta={"email": user.email, "role": user.role.value, "is_active": user.is_active},
    )
    return UserRead.model_validate(user)


@router.post("/invite", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def invite_user(body: UserInvite, admin: AdminUser, db: SessionDep) -> UserRead:
    """Create an invited user and send them the link. Admin-only.

    409 on a taken address in every state — active, inactive, or already
    invited. The fix for "they never got it" is resend, not a second invite.
    """
    # Check email before creating the row, so a misconfigured instance does
    # not accumulate invited users nobody can reach.
    row = await settings_service.get_instance_settings(db)
    if not (row.smtp_enabled and row.app_url):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_MAIL_NOT_CONFIGURED)
    try:
        user = await user_service.invite_user(
            db, email=body.email, full_name=body.full_name, role=body.role
        )
    except user_service.EmailAlreadyExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with that email already exists",
        ) from None
    await _send_invitation(db, user, inviter=admin)
    await _audit(
        db,
        actor=admin,
        action=AuditAction.create,
        object_id=user.id,
        meta={"email": user.email, "role": user.role.value, "invited": True},
    )
    return UserRead.model_validate(user)


# --- admin: single user -------------------------------------------------------


@router.post("/{user_id}/invite", status_code=status.HTTP_204_NO_CONTENT)
async def resend_invitation(user_id: int, admin: AdminUser, db: SessionDep) -> None:
    """Send a fresh invitation to a user who has not yet accepted. Admin-only.

    Refused for an accepted user: they have a password, and re-inviting them
    would hand anyone with their inbox a way to reset it.
    """
    user = await _get_or_404(db, user_id)
    if user.invited_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That user has already accepted their invitation.",
        )
    await _send_invitation(db, user, inviter=admin)
    await _audit(
        db,
        actor=admin,
        action=AuditAction.update,
        object_id=user.id,
        meta={"invitation_resent": True},
    )


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: int,
    body: UserUpdate,
    admin: AdminUser,
    db: SessionDep,
) -> UserRead:
    """Partially update another user (name, role, active). Admin-only.

    409 when the change would lock the caller out or remove the last admin.
    """
    user = await _get_or_404(db, user_id)
    try:
        user, changes = await user_service.update_user(
            db,
            user,
            actor=admin,
            full_name=body.full_name,
            role=body.role,
            is_active=body.is_active,
        )
    except user_service.UserProtectionError as exc:
        raise _conflict(exc) from None
    if changes:
        action = _action_for(changes)
        meta = {"changes": changes} if action == AuditAction.update else None
        await _audit(db, actor=admin, action=action, object_id=user.id, meta=meta)
    return UserRead.model_validate(user)


@router.put("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    user_id: int,
    body: PasswordReset,
    admin: AdminUser,
    db: SessionDep,
) -> None:
    """Set a new password for another user. Admin-only."""
    user = await _get_or_404(db, user_id)
    await user_service.set_password(db, user, body.password)
    await _audit(
        db, actor=admin, action=AuditAction.update, object_id=user.id, meta={"password_reset": True}
    )


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    admin: AdminUser,
    db: SessionDep,
) -> None:
    """Hard-delete a user. Admin-only. 409 under the lock-out rules."""
    user = await _get_or_404(db, user_id)
    snapshot = {"email": user.email, "role": user.role.value}
    try:
        await user_service.delete_user(db, user, actor=admin)
    except user_service.UserProtectionError as exc:
        raise _conflict(exc) from None
    await _audit(db, actor=admin, action=AuditAction.delete, object_id=user_id, meta=snapshot)


@router.post("/{user_id}/totp/disable", status_code=status.HTTP_204_NO_CONTENT)
async def admin_totp_disable(user_id: int, admin: AdminUser, db: SessionDep) -> None:
    """Turn off another user's 2FA. Admin-only; no code — this is the
    lost-phone backstop. The user is told by email, naming the admin."""
    user = await _get_or_404(db, user_id)
    try:
        await totp.disable(db, user)
    except totp.TotpNotEnabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Two-factor authentication is not on for that user.",
        ) from None
    await _audit(
        db,
        actor=admin,
        action=AuditAction.update,
        object_id=user.id,
        meta={"totp": "disabled_by_admin"},
    )
    send_email_task.delay(
        to=user.email,
        template="totp_disabled",
        subject=f"Two-factor authentication was turned off on your {APP_NAME} account",
        context={"app_name": APP_NAME, "admin_name": admin.full_name.strip() or admin.email},
    )
