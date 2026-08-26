"""User management routes.

Listing and creating users are admin-only (RBAC). ``GET /users/me`` returns the
caller and is available to any authenticated user.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.schemas.user import UserCreate, UserRead
from app.services import user as user_service

router = APIRouter(tags=["users"])


@router.get("/me", response_model=UserRead)
async def read_current_user(current_user: CurrentUser) -> UserRead:
    """Return the authenticated caller."""
    return UserRead.model_validate(current_user)


@router.get("", response_model=list[UserRead])
async def list_users(
    _admin: AdminUser,
    db: SessionDep,
) -> list[UserRead]:
    """List all users. Admin-only."""
    users = await user_service.list_users(db)
    return [UserRead.model_validate(u) for u in users]


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    _admin: AdminUser,
    db: SessionDep,
) -> UserRead:
    """Create a user with an explicit role. Admin-only."""
    try:
        user = await user_service.create_user(
            db,
            email=body.email,
            password=body.password,
            full_name=body.full_name,
            role=body.role,
            is_active=body.is_active,
        )
    except user_service.EmailAlreadyExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with that email already exists",
        ) from None
    return UserRead.model_validate(user)
