"""Authentication routes: login, token refresh, and current-user."""

from __future__ import annotations

import jwt
from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, SessionDep
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.schemas.auth import LoginRequest, RefreshRequest, TokenPair
from app.schemas.user import UserRead
from app.services import user as user_service

router = APIRouter(tags=["auth"])


def _issue_tokens(user_id: int, role: str) -> TokenPair:
    return TokenPair(
        access_token=create_access_token(user_id, role),
        refresh_token=create_refresh_token(user_id),
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
    return _issue_tokens(user.id, user.role.value)


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
    return _issue_tokens(user.id, user.role.value)


@router.get("/me", response_model=UserRead)
async def read_me(current_user: CurrentUser) -> UserRead:
    """Return the currently authenticated user."""
    return UserRead.model_validate(current_user)
