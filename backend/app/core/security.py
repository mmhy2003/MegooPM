"""Security primitives: password hashing and JWT issuance/verification.

Password hashing uses **Argon2id** (via ``argon2-cffi``), the current OWASP-
recommended default. JWTs are signed with HMAC (``HS256``) using
``settings.secret_key``.

This module is intentionally framework-agnostic (no FastAPI imports) so it can
be reused by services, scripts, and tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import settings

# A single, process-wide hasher. Argon2 parameters are the library defaults,
# which follow current OWASP guidance; tune here if profiling demands it.
_password_hasher = PasswordHasher()

TokenType = Literal["access", "refresh"]


def hash_password(password: str) -> str:
    """Return an Argon2id hash (with embedded salt and parameters)."""
    return _password_hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Verify ``password`` against an Argon2 ``hashed`` value.

    Returns ``False`` for a mismatch or a malformed/foreign hash rather than
    raising, so callers can treat authentication failures uniformly.
    """
    try:
        return _password_hasher.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(hashed: str) -> bool:
    """Whether ``hashed`` should be re-computed with the current parameters."""
    try:
        return _password_hasher.check_needs_rehash(hashed)
    except (InvalidHashError, ValueError):
        return False


def _create_token(
    subject: str,
    token_type: TokenType,
    expires_delta: timedelta,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(subject: str | int, role: str, *, token_version: int) -> str:
    """Issue a short-lived access token carrying the user's ``role``.

    ``tv`` is the user's token_version at issue. Access tokens are not checked
    against it — they live minutes — but carrying it keeps both token types the
    same shape, and a future check costs no re-issue.
    """
    return _create_token(
        subject=str(subject),
        token_type="access",
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
        extra_claims={"role": role, "tv": token_version},
    )


def create_refresh_token(subject: str | int, *, token_version: int) -> str:
    """Issue a longer-lived refresh token (no role claim; role is re-read on use).

    ``tv`` is what lets a password change end this session: refresh refuses a
    token whose version no longer matches the user's.
    """
    return _create_token(
        subject=str(subject),
        token_type="refresh",
        expires_delta=timedelta(minutes=settings.refresh_token_expire_minutes),
        extra_claims={"tv": token_version},
    )


def decode_token(token: str, *, expected_type: TokenType) -> dict[str, Any]:
    """Decode and validate a JWT, enforcing signature, expiry, and token type.

    Raises :class:`jwt.PyJWTError` (or a subclass) on any validation failure,
    including a ``type`` claim that does not match ``expected_type``.
    """
    payload = jwt.decode(
        token,
        settings.secret_key,
        algorithms=[settings.jwt_algorithm],
    )
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(f"expected {expected_type} token, got {payload.get('type')!r}")
    if not payload.get("sub"):
        raise jwt.InvalidTokenError("token missing subject")
    return payload
