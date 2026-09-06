"""A long-lived credential one user hands to a machine.

SHA-256, not Argon2 — deliberately the opposite of :mod:`app.models.recovery_code`.
A recovery code is ten characters, about fifty bits, and needs a slow hash to
survive an offline attack on a leaked table. A token here is 256 bits of CSPRNG
output: there is nothing to brute force, and Argon2 costs roughly 60ms and 64MB
*per verification*, which here means per API request. Using it would turn every
authenticated call into a self-inflicted denial of service while buying nothing.

``token_prefix`` is the lookup handle, so verifying a key is one indexed
single-row read and one constant-time compare. It is also what the UI shows, so
a key can be matched to a log line without revealing it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin


class ApiKey(IdMixin, Base):
    """One key. The token itself exists only in the response that minted it."""

    __tablename__ = "api_key"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    #: "mgm_" + the first 8 characters of the secret. Unique, so a lookup is one row.
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False, unique=True, index=True)
    #: SHA-256 hex of the whole token. Never the token.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    #: NULL means the key never expires — a deliberate choice, not a default.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["ApiKey"]
