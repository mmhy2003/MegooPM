"""Single-use, expiring secrets bound to a user.

One table for every kind — password reset today, invitations next — because
they are the same shape: a hashed token, an owner, an expiry, and whether it
has been spent. A second table per kind would be a copy.

Only the hash is stored. A database leak must not hand over live reset links,
for the same reason it must not hand over passwords.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import AuthTokenKind
from app.models.mixins import IdMixin


class AuthToken(IdMixin, Base):
    """One issued token. ``used_at`` set means spent."""

    __tablename__ = "auth_token"

    kind: Mapped[AuthTokenKind] = mapped_column(
        Enum(
            AuthTokenKind,
            name="auth_token_kind",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    # SHA-256 hex. Unique so a lookup by hash is an index hit, and so two
    # tokens can never collide into one row.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # CASCADE: a deleted user's outstanding tokens are meaningless.
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["AuthToken"]
