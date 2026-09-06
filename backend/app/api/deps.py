"""Shared FastAPI dependencies for authentication and authorization.

- :func:`get_current_user` resolves the bearer credential — a session's access
  token *or* an API key — to a live, active :class:`User` (401 on any failure).
- :func:`require_admin` gates admin-only routes (403 for non-admins).

A key is recognised by its ``mgm_`` prefix, which is why every route accepts one
without a line of its own.
"""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_context import AuthKey, current_api_key
from app.core.config import settings
from app.core.security import decode_token
from app.db.session import get_session
from app.models.user import User
from app.services import api_keys
from app.services import user as user_service

# ``tokenUrl`` powers Swagger's "Authorize" button. Login also accepts JSON
# (see routes/auth.py); this only declares where a token can be obtained.
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.api_v1_prefix}/auth/login",
    auto_error=False,
)

# Request-scoped DB session, as a reusable annotated dependency.
SessionDep = Annotated[AsyncSession, Depends(get_session)]

_CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def _key_rejected(detail: str) -> HTTPException:
    """A 401 that names the reason.

    Only reachable once the digest matched, so the caller demonstrably holds the
    key and is told nothing they could not already infer — while a CI job that
    broke at 3am is told why.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _user_from_key(db: AsyncSession, token: str) -> User:
    """Resolve an API key to its owner, or raise 401 with a usable reason."""
    try:
        key = await api_keys.authenticate(db, token)
    except api_keys.KeyDisabled:
        raise _key_rejected("This API key is disabled.") from None
    except api_keys.KeyExpired:
        raise _key_rejected("This API key has expired.") from None
    except api_keys.KeyRejected:
        # An unknown prefix and a wrong digest are one case on purpose: a guess
        # must not confirm that a real key exists.
        raise _CREDENTIALS_EXCEPTION from None

    user = await user_service.get_by_id(db, key.user_id)
    if user is None or not user.is_active:
        raise _CREDENTIALS_EXCEPTION
    await api_keys.touch(db, key)
    current_api_key.set(AuthKey(id=key.id, name=key.name))
    return user


async def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Resolve the bearer credential: a session's access token, or an API key."""
    if not token:
        raise _CREDENTIALS_EXCEPTION
    if token.startswith(api_keys.TOKEN_PREFIX):
        return await _user_from_key(db, token)

    # Not a key — but set the marker anyway, so nothing an earlier request left
    # in this context can be read as "this request used a key".
    current_api_key.set(None)
    try:
        payload = decode_token(token, expected_type="access")
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise _CREDENTIALS_EXCEPTION from None

    user = await user_service.get_by_id(db, user_id)
    if user is None or not user.is_active:
        raise _CREDENTIALS_EXCEPTION
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(current_user: CurrentUser) -> User:
    """Ensure the current user is an admin; otherwise raise 403."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user


AdminUser = Annotated[User, Depends(require_admin)]

__all__ = [
    "AdminUser",
    "CurrentUser",
    "SessionDep",
    "get_current_user",
    "require_admin",
]
