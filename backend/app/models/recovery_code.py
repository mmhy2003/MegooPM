"""One-time codes for signing in without the authenticator app.

Argon2id, not SHA-256: a recovery code is ten characters — about fifty bits.
That survives a rate-limited guess over the network and does not survive an
offline attack on a fast hash if this table leaks.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin


class RecoveryCode(IdMixin, Base):
    """One code. ``used_at`` set means spent."""

    __tablename__ = "recovery_code"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["RecoveryCode"]
