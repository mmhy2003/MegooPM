"""Authentication routes: login, token refresh, and current-user."""

from __future__ import annotations

import jwt
from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import CurrentUser, SessionDep
from app.core.client_ip import client_ip
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.models.enums import AuditAction, AuthTokenKind
from app.models.user import User
from app.schemas.auth import (
    AcceptInviteRequest,
    AuthCapabilities,
    ForgotPasswordRequest,
    LoginRequest,
    NeutralResponse,
    RefreshRequest,
    ResetPasswordRequest,
    TokenPair,
)
from app.schemas.user import UserRead
from app.services import auth_tokens, rate_limit
from app.services import instance_settings as settings_service
from app.services import user as user_service
from app.services.audit import record_audit
from app.services.mail.templates import APP_NAME
from app.tasks.mail import send_email as send_email_task

router = APIRouter(tags=["auth"])


def _issue_tokens(user: User) -> TokenPair:
    return TokenPair(
        access_token=create_access_token(
            user.id, user.role.value, token_version=user.token_version
        ),
        refresh_token=create_refresh_token(user.id, token_version=user.token_version),
    )


@router.post("/login", response_model=TokenPair)
async def login(
    body: LoginRequest,
    db: SessionDep,
) -> TokenPair:
    """Authenticate with email + password and return an access/refresh pair."""
    user = await user_service.authenticate(db, email=body.email, password=body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _issue_tokens(user)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    body: RefreshRequest,
    db: SessionDep,
) -> TokenPair:
    """Exchange a valid refresh token for a fresh access/refresh pair.

    The refresh token is rotated. The user's role is re-read from the database
    so privilege changes take effect on the next refresh.
    """
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(body.refresh_token, expected_type="refresh")
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise invalid from None

    user = await user_service.get_by_id(db, user_id)
    if user is None or not user.is_active:
        raise invalid
    # A password change bumps the user's version; a refresh token minted
    # before it carries the old one. Refusing here is what makes "reset my
    # password" also mean "end the sessions I did not start".
    if payload.get("tv") != user.token_version:
        raise invalid
    return _issue_tokens(user)


@router.get("/me", response_model=UserRead)
async def read_me(current_user: CurrentUser) -> UserRead:
    """Return the currently authenticated user."""
    return UserRead.model_validate(current_user)


# --- password reset ----------------------------------------------------------

NEUTRAL_MESSAGE = "If that address is registered, a reset link is on its way."


async def _reset_available(db) -> tuple[bool, str | None]:
    """Whether a reset link can be built and sent, and the app URL if so."""
    row = await settings_service.get_instance_settings(db)
    if row.smtp_enabled and row.app_url:
        return True, row.app_url
    return False, None


@router.get("/capabilities", response_model=AuthCapabilities)
async def capabilities(db: SessionDep) -> AuthCapabilities:
    """What the login page may offer. Unauthenticated by necessity.

    Leaks one bit — whether email is configured — which is cheaper than a user
    clicking "forgot password", being told to check their inbox, and nothing
    ever arriving.
    """
    available, _ = await _reset_available(db)
    return AuthCapabilities(password_reset=available)


def _limit(exc: rate_limit.RateLimited) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many requests. Try again later.",
        headers={"Retry-After": str(exc.retry_after)},
    )


def _unavailable() -> HTTPException:
    # A fresh instance each time: raising one shared exception object from
    # several places leaves the last traceback attached to all of them.
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Password reset is temporarily unavailable.",
    )


@router.post(
    "/forgot-password", response_model=NeutralResponse, status_code=status.HTTP_202_ACCEPTED
)
async def forgot_password(
    body: ForgotPasswordRequest, request: Request, db: SessionDep
) -> NeutralResponse:
    """Issue a reset link if the address belongs to an active account.

    Returns the same status and body whether or not it does. Otherwise this
    page is a directory of who has an account, for anyone who can reach it.
    Response *timing* still differs slightly; the spec records that as a
    known, accepted gap.
    """
    try:
        await rate_limit.check_password_reset(email=body.email, ip=client_ip(request))
    except rate_limit.RateLimited as exc:
        raise _limit(exc) from None
    except rate_limit.RateLimitUnavailable:
        raise _unavailable() from None

    available, app_url = await _reset_available(db)
    user = await user_service.get_by_email(db, body.email)
    if available and user is not None and user.is_active:
        raw = await auth_tokens.issue(
            db, user=user, kind=AuthTokenKind.password_reset, ttl=auth_tokens.RESET_TTL
        )
        send_email_task.delay(
            to=user.email,
            template="password_reset",
            subject=f"Reset your {APP_NAME} password",
            context={
                "app_name": APP_NAME,
                "reset_url": f"{app_url}/reset-password?token={raw}",
                "ttl_minutes": int(auth_tokens.RESET_TTL.total_seconds() // 60),
            },
        )
    return NeutralResponse(detail=NEUTRAL_MESSAGE)


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(body: ResetPasswordRequest, request: Request, db: SessionDep) -> None:
    """Spend a reset token and set the new password. Ends every open session."""
    try:
        await rate_limit.check_password_reset_redeem(ip=client_ip(request))
    except rate_limit.RateLimited as exc:
        raise _limit(exc) from None
    except rate_limit.RateLimitUnavailable:
        raise _unavailable() from None

    try:
        row = await auth_tokens.redeem(db, raw=body.token, kind=AuthTokenKind.password_reset)
    except auth_tokens.TokenInvalid as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    user = await user_service.get_by_id(db, row.user_id)
    if user is None or not user.is_active:
        # The token was valid a moment ago and the account is gone or off.
        # Same message as every other refusal.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(auth_tokens.TokenInvalid())
        )

    # set_password bumps token_version, which is what ends the other sessions.
    await user_service.set_password(db, user, body.new_password)
    await record_audit(
        db,
        actor=user.email,
        action=AuditAction.update,
        object_type="user",
        object_id=user.id,
        meta={"password_reset_via_email": True},
    )
    await db.commit()
    send_email_task.delay(
        to=user.email,
        template="password_changed",
        subject=f"Your {APP_NAME} password was changed",
        context={"app_name": APP_NAME},
    )


@router.post("/accept-invite", status_code=status.HTTP_204_NO_CONTENT)
async def accept_invite(body: AcceptInviteRequest, request: Request, db: SessionDep) -> None:
    """Spend an invitation token: set the name and password, activate.

    Then the invitee goes to the login page rather than into a session, for
    the same reason as a reset: the token arrived by email.
    """
    try:
        await rate_limit.check_password_reset_redeem(ip=client_ip(request))
    except rate_limit.RateLimited as exc:
        raise _limit(exc) from None
    except rate_limit.RateLimitUnavailable:
        raise _unavailable() from None

    try:
        row = await auth_tokens.redeem(db, raw=body.token, kind=AuthTokenKind.invitation)
    except auth_tokens.TokenInvalid as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    user = await user_service.get_by_id(db, row.user_id)
    if user is None or user.invited_at is None:
        # Deleted (revoked) since the email went out, or already accepted.
        # Same message as every other refusal.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(auth_tokens.TokenInvalid())
        )

    await user_service.accept_invitation(
        db, user, full_name=body.full_name, password=body.password
    )
    await record_audit(
        db,
        actor=user.email,
        action=AuditAction.update,
        object_type="user",
        object_id=user.id,
        meta={"invitation_accepted": True},
    )
    await db.commit()
