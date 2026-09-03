"""Authentication routes: login, token refresh, and current-user."""

from __future__ import annotations

import jwt
from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import CurrentUser, SessionDep
from app.core.client_ip import client_ip
from app.core.security import (
    create_access_token,
    create_mfa_token,
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
    MfaRequired,
    MfaVerifyRequest,
    MfaVerifyResponse,
    NeutralResponse,
    PasskeyAssertRequest,
    PasskeyOptions,
    PasskeyOptionsRequest,
    RefreshRequest,
    ResetPasswordRequest,
    TokenPair,
)
from app.schemas.user import UserRead
from app.services import auth_tokens, passkeys, rate_limit, totp, webauthn_challenge
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


@router.post("/login", response_model=TokenPair | MfaRequired)
async def login(
    body: LoginRequest,
    db: SessionDep,
) -> TokenPair | MfaRequired:
    """Authenticate with email + password.

    Returns a token pair — or, for a user with 2FA on, a five-minute
    ``mfa_token`` to present with a code at ``/auth/mfa/verify``. A wrong
    password is 401 either way: the challenge must not leak that the password
    was right.
    """
    user = await user_service.authenticate(db, email=body.email, password=body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user.totp_enabled:
        methods: list[str] = ["totp"]
        if await passkeys.list_for(db, user):
            methods.append("passkey")
        return MfaRequired(
            mfa_token=create_mfa_token(user.id, token_version=user.token_version),
            methods=methods,  # type: ignore[arg-type]
        )
    return _issue_tokens(user)


@router.post("/mfa/verify", response_model=MfaVerifyResponse)
async def mfa_verify(body: MfaVerifyRequest, request: Request, db: SessionDep) -> MfaVerifyResponse:
    """Exchange a challenge token plus a code for the real token pair.

    One message for every refusal — bad token, expired token, wrong code,
    replayed code, spent recovery code. Any distinction tells an attacker
    which part they got right.
    """
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=totp.INVALID_CODE_MESSAGE,
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(body.mfa_token, expected_type="mfa")
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise invalid from None

    try:
        await rate_limit.check_mfa_verify(user_id=user_id, ip=client_ip(request))
    except rate_limit.RateLimited as exc:
        raise _limit(exc) from None
    except rate_limit.RateLimitUnavailable:
        raise _unavailable() from None

    user = await user_service.get_by_id(db, user_id)
    if user is None or not user.is_active or payload.get("tv") != user.token_version:
        raise invalid
    if not await totp.verify_code(db, user, body.code):
        raise invalid

    # Only a recovery code changes the remaining count; a TOTP reports None
    # so the client does not nag after every ordinary sign-in.
    remaining = (
        None if totp.is_totp_shaped(body.code) else await totp.recovery_codes_remaining(db, user)
    )
    pair = _issue_tokens(user)
    return MfaVerifyResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        recovery_codes_remaining=remaining,
    )


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
    row = await settings_service.get_instance_settings(db)
    try:
        passkeys.relying_party(row.app_url)
        passkeys_available = True
    except passkeys.PasskeysUnavailable:
        passkeys_available = False
    return AuthCapabilities(
        password_reset=bool(row.smtp_enabled and row.app_url), passkeys=passkeys_available
    )


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

    await user_service.accept_invitation(db, user, full_name=body.full_name, password=body.password)
    await record_audit(
        db,
        actor=user.email,
        action=AuditAction.update,
        object_type="user",
        object_id=user.id,
        meta={"invitation_accepted": True},
    )
    await db.commit()


# --- second factor: passkeys ------------------------------------------------------

PASSKEY_REFUSED = "That passkey was not accepted."


def _passkey_refused() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=PASSKEY_REFUSED,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _store_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Try again in a moment."
    )


async def _mfa_subject(body_token: str, request: Request, db: SessionDep) -> User:
    """Decode an mfa token, apply the limiter, and load a live user — or refuse.

    Shared by the two passkey routes. One message for every failure.
    """
    try:
        payload = decode_token(body_token, expected_type="mfa")
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise _passkey_refused() from None
    try:
        await rate_limit.check_mfa_verify(user_id=user_id, ip=client_ip(request))
    except rate_limit.RateLimited as exc:
        raise _limit(exc) from None
    except rate_limit.RateLimitUnavailable:
        raise _unavailable() from None
    user = await user_service.get_by_id(db, user_id)
    if user is None or not user.is_active or payload.get("tv") != user.token_version:
        raise _passkey_refused()
    return user


async def _login_relying_party(db: SessionDep) -> passkeys.RelyingParty:
    row = await settings_service.get_instance_settings(db)
    try:
        return passkeys.relying_party(row.app_url)
    except passkeys.PasskeysUnavailable:
        raise _passkey_refused() from None


@router.post("/mfa/passkey/options", response_model=PasskeyOptions)
async def mfa_passkey_options(
    body: PasskeyOptionsRequest, request: Request, db: SessionDep
) -> PasskeyOptions:
    """Options for answering the challenge with a passkey."""
    user = await _mfa_subject(body.mfa_token, request, db)
    rows = await passkeys.list_for(db, user)
    if not rows:
        raise _passkey_refused()
    rp = await _login_relying_party(db)
    options, challenge = passkeys.authentication_options(rp, passkeys=rows)
    try:
        nonce = await webauthn_challenge.put(
            kind="authenticate", user_id=user.id, challenge=challenge
        )
    except webauthn_challenge.ChallengeStoreUnavailable:
        raise _store_unavailable() from None
    return PasskeyOptions(nonce=nonce, options=options)


@router.post("/mfa/passkey/verify", response_model=MfaVerifyResponse)
async def mfa_passkey_verify(
    body: PasskeyAssertRequest, request: Request, db: SessionDep
) -> MfaVerifyResponse:
    """Exchange the challenge token plus a passkey assertion for the real pair."""
    user = await _mfa_subject(body.mfa_token, request, db)
    rp = await _login_relying_party(db)
    try:
        stored = await webauthn_challenge.take(kind="authenticate", nonce=body.nonce)
    except webauthn_challenge.ChallengeStoreUnavailable:
        raise _store_unavailable() from None
    if stored is None or stored[0] != user.id:
        raise _passkey_refused()
    wanted = passkeys.credential_id_of(body.credential)
    row = next((p for p in await passkeys.list_for(db, user) if p.credential_id == wanted), None)
    if row is None:
        raise _passkey_refused()
    try:
        new_count = passkeys.verify_authentication(
            rp, credential=body.credential, challenge=stored[1], passkey=row
        )
    except passkeys.PasskeyRejected:
        raise _passkey_refused() from None
    await passkeys.touch(db, row, new_count)
    pair = _issue_tokens(user)
    return MfaVerifyResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        recovery_codes_remaining=None,
    )
