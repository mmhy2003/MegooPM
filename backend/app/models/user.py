"""User accounts and roles for authentication / RBAC.

A :class:`User` is an authenticatable principal. ``role`` drives role-based
access control: :attr:`UserRole.admin` may perform privileged actions;
:attr:`UserRole.member` is a limited user.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, Integer, String, Text
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
    # Bumped on every password change. Both token types carry the value at
    # issue; refresh refuses a mismatch, so a reset ends every session the
    # user had open instead of leaving them for seven days. (Deactivation is
    # handled separately: refresh already refuses an inactive user.)
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Set while an invitation is outstanding; cleared on accept. This is the
    # one definition of "invited" — no status enum beside is_active, because
    # two sources of truth is how "off" and "invited" drift apart.
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Two-factor authentication ---------------------------------------
    # Fernet token (app.core.crypto), never plaintext. A secret with no
    # enabled_at is a *pending* enrolment: shown, but never proven to work.
    # Login ignores it entirely, so an abandoned setup locks nobody out.
    totp_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The time-step of the last code accepted. A code whose step is not later
    # is refused even if correct: a code is valid for up to ninety seconds
    # under the drift window, which is ninety seconds to replay one seen over
    # a shoulder. PyOTP does not track this; the service does.
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    @property
    def totp_enabled(self) -> bool:
        """Whether a second factor is required at login."""
        return self.totp_enabled_at is not None

    @property
    def is_admin(self) -> bool:
        """Whether this user holds the admin role."""
        return self.role == UserRole.admin


__all__ = ["User", "UserRole"]
