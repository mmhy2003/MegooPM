"""Pydantic schemas for users.

ORM ``User`` rows are never returned directly; :class:`UserRead` is the public
projection (note: it deliberately omits ``hashed_password``).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

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


class UserInvite(BaseModel):
    """Payload to invite a user. No password: they choose one when they accept."""

    email: EmailStr
    full_name: str = Field(default="", max_length=255)
    role: UserRole = UserRole.member


class UserRead(UserBase):
    """Public representation of a user."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
    # Set while an invitation is outstanding. The users table renders the
    # Invited badge and the resend action from this alone.
    invited_at: datetime | None = None


class UserUpdate(BaseModel):
    """Admin partial update of another user.

    ``email`` is identity and immutable, so it is *rejected* (``extra="forbid"``)
    rather than silently ignored. At least one field must be present.
    """

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, max_length=255)
    role: UserRole | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _require_a_field(self) -> UserUpdate:
        if self.full_name is None and self.role is None and self.is_active is None:
            raise ValueError("Provide at least one of full_name, role, is_active.")
        return self


class PasswordReset(BaseModel):
    """Admin-set password for another user (handed over out of band)."""

    password: str = Field(min_length=8, max_length=128)


class PasswordChange(BaseModel):
    """Self-service password change. The signed-in session is the only proof
    required — the current password is deliberately not re-verified."""

    new_password: str = Field(min_length=8, max_length=128)


class ProfileUpdate(BaseModel):
    """Self-service profile edit. Only the display name is user-editable."""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(max_length=255)


__all__ = [
    "PasswordChange",
    "PasswordReset",
    "ProfileUpdate",
    "UserBase",
    "UserCreate",
    "UserInvite",
    "UserRead",
    "UserUpdate",
]
