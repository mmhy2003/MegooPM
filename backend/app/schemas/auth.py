"""Pydantic schemas for authentication (login, tokens, refresh)."""

from __future__ import annotations

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


class ForgotPasswordRequest(BaseModel):
    """Body for ``POST /auth/forgot-password``."""

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Body for ``POST /auth/reset-password``."""

    token: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=128)


class AuthCapabilities(BaseModel):
    """What the login page may offer before anyone is signed in."""

    password_reset: bool


class NeutralResponse(BaseModel):
    """The one body ``forgot-password`` ever returns."""

    detail: str


__all__ = [
    "AuthCapabilities",
    "ForgotPasswordRequest",
    "LoginRequest",
    "NeutralResponse",
    "RefreshRequest",
    "ResetPasswordRequest",
    "TokenPair",
]
