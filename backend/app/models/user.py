"""User accounts and roles for authentication / RBAC.

A :class:`User` is an authenticatable principal. ``role`` drives role-based
access control: :attr:`UserRole.admin` may perform privileged actions;
:attr:`UserRole.member` is a limited user.
"""

from __future__ import annotations

import enum

from sqlalchemy import Boolean, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin


class UserRole(enum.StrEnum):
    """Access-control role for a user."""

    admin = "admin"
    member = "member"


class User(IdMixin, TimestampMixin, Base):
    """An authenticatable user account."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(
        String(255), nullable=False, default="", server_default=""
    )
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=UserRole.member,
        server_default=UserRole.member.value,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    @property
    def is_admin(self) -> bool:
        """Whether this user holds the admin role."""
        return self.role == UserRole.admin


__all__ = ["User", "UserRole"]
