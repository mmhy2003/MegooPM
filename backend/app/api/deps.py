"""Shared FastAPI dependencies for authentication and authorization.

- :func:`get_current_user` resolves the bearer access token to a live, active
  :class:`User` (401 on any failure).
- :func:`require_admin` gates admin-only routes (403 for non-admins).
"""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decode_token
from app.db.session import get_session
from app.models.user import User
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


async def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Resolve and validate the current user from the bearer access token."""
    if not token:
        raise _CREDENTIALS_EXCEPTION
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
