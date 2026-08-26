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


__all__ = ["LoginRequest", "RefreshRequest", "TokenPair"]
