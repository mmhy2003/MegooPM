"""Pydantic schemas for users.

ORM ``User`` rows are never returned directly; :class:`UserRead` is the public
projection (note: it deliberately omits ``hashed_password``).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import UserRole


class UserBase(BaseModel):
    """Fields shared by user read/write schemas."""

    email: EmailStr
    full_name: str = ""
    role: UserRole = UserRole.member
    is_active: bool = True


class UserCreate(UserBase):
    """Payload to create a user (admin-only endpoint)."""

    password: str = Field(min_length=8, max_length=128)


class UserRead(UserBase):
    """Public representation of a user."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


__all__ = ["UserBase", "UserCreate", "UserRead"]
