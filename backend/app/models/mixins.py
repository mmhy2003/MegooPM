"""Reusable column mixins for ORM models.

Every aggregate table gets a ``BigInteger`` surrogate ``id`` and
``created_at``/``updated_at`` timestamps. ``updated_at`` is refreshed server-side
via ``onupdate=func.now()`` on every UPDATE.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class IdMixin:
    """Surrogate ``BigInteger`` primary key named ``id``."""

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)


class TimestampMixin:
    """``created_at`` / ``updated_at`` audit timestamps (timezone-aware)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


__all__ = ["IdMixin", "TimestampMixin"]
