"""Pydantic schemas for authentication (login, tokens, refresh)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """Credentials submitted to ``POST /auth/login``."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    """Body submitted to ``POST /auth/refresh``."""

    refresh_token: str


class TokenPair(BaseModel):
    """Issued access + refresh tokens.

    ``token_type`` is the OAuth2 scheme name (``bearer``); clients send the
    access token as ``Authorization: Bearer <access_token>``.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


MfaMethod = Literal["totp", "passkey"]


class MfaRequired(BaseModel):
    """What ``POST /auth/login`` returns when a second factor is needed.

    ``mfa_required`` is a literal so the frontend can discriminate the union
    without inspecting which keys are present. ``methods`` tells the form
    what to offer without a second request.
    """

    mfa_required: Literal[True] = True
    mfa_token: str
    methods: list[MfaMethod] = Field(default_factory=lambda: ["totp"])


class PasskeyOptionsRequest(BaseModel):
    """Body for ``POST /auth/mfa/passkey/options``."""

    mfa_token: str = Field(min_length=1)


class PasskeyOptions(BaseModel):
    """A ceremony's options, plus the nonce that names its stored challenge."""

    nonce: str
    options: dict[str, Any]


class PasskeyAssertRequest(BaseModel):
    """Body for ``POST /auth/mfa/passkey/verify``."""

    mfa_token: str = Field(min_length=1)
    nonce: str = Field(min_length=1, max_length=128)
    credential: dict[str, Any]


class MfaVerifyRequest(BaseModel):
    """Body for ``POST /auth/mfa/verify``."""

    mfa_token: str = Field(min_length=1)
    code: str = Field(min_length=1, max_length=32)


class MfaVerifyResponse(TokenPair):
    """The real pair, plus how many recovery codes are left when one was used."""

    recovery_codes_remaining: int | None = None


class ForgotPasswordRequest(BaseModel):
    """Body for ``POST /auth/forgot-password``."""

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Body for ``POST /auth/reset-password``."""

    token: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=128)


class AcceptInviteRequest(BaseModel):
    """Body for ``POST /auth/accept-invite``."""

    token: str = Field(min_length=1, max_length=256)
    full_name: str = Field(default="", max_length=255)
    password: str = Field(min_length=8, max_length=128)


class AuthCapabilities(BaseModel):
    """What the login page may offer before anyone is signed in."""

    password_reset: bool
    # Whether the backend can act as a WebAuthn relying party (app URL set).
    passkeys: bool


class NeutralResponse(BaseModel):
    """The one body ``forgot-password`` ever returns."""

    detail: str


__all__ = [
    "AcceptInviteRequest",
    "AuthCapabilities",
    "ForgotPasswordRequest",
    "LoginRequest",
    "MfaMethod",
    "MfaRequired",
    "MfaVerifyRequest",
    "MfaVerifyResponse",
    "NeutralResponse",
    "PasskeyAssertRequest",
    "PasskeyOptions",
    "PasskeyOptionsRequest",
    "RefreshRequest",
    "ResetPasswordRequest",
    "TokenPair",
]
