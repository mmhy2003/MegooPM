"""A registered WebAuthn credential — a passkey — for one user.

Stores exactly what the verify step needs: the credential id to match an
assertion, the public key to check its signature, and the last sign count to
notice a cloned authenticator. Nothing here is secret; nothing here is enough
to sign in.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin


class Passkey(IdMixin, Base):
    __tablename__ = "passkey"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sign_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String(64), nullable=False, default="Passkey")
    # Transport hints from registration, replayed in allowCredentials so the
    # browser knows whether to look at a USB key or the platform.
    transports: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = ["Passkey"]
