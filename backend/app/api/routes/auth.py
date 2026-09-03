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
from app.models.user import User
from app.schemas.auth import LoginRequest, RefreshRequest, TokenPair
from app.schemas.user import UserRead
from app.services import user as user_service

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
