"""Append-only audit log of domain mutations."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import AuditAction
from app.models.mixins import IdMixin


class AuditLog(IdMixin, Base):
    """One row per mutation to a domain object.

    Append-only: rows are never updated, so there is no ``updated_at``. ``actor``
    is nullable to accommodate system-initiated changes. ``object_id`` is a loose
    reference (no FK) so history survives deletion of the referenced row.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_object", "object_type", "object_id"),
        Index("ix_audit_log_created_at", "created_at"),
    )

    # Who performed the change (username/email); null for system actions.
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[AuditAction] = mapped_column(
        Enum(
            AuditAction,
            name="audit_action",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    # e.g. "proxy_host", "upstream", "certificate".
    object_type: Mapped[str] = mapped_column(String(100), nullable=False)
    object_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Structured details / before-after diff.
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["AuditLog"]
