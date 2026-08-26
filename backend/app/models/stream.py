"""Streams — raw TCP/UDP port forwarding."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.certificate import Certificate


class Stream(IdMixin, TimestampMixin, Base):
    """Forwards an incoming TCP and/or UDP port to a backend host:port."""

    __tablename__ = "streams"
    __table_args__ = (
        CheckConstraint("incoming_port BETWEEN 1 AND 65535", name="incoming_port_range"),
        CheckConstraint("forward_port BETWEEN 1 AND 65535", name="forward_port_range"),
        # At least one protocol must be enabled for the stream to do anything.
        CheckConstraint("tcp_forwarding OR udp_forwarding", name="at_least_one_protocol"),
    )

    # Only one stream may claim a given incoming port.
    incoming_port: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    forward_host: Mapped[str] = mapped_column(String(255), nullable=False)
    forward_port: Mapped[int] = mapped_column(Integer, nullable=False)
    tcp_forwarding: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    udp_forwarding: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    certificate_id: Mapped[int | None] = mapped_column(
        ForeignKey("certificates.id", ondelete="SET NULL"), nullable=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    certificate: Mapped[Certificate | None] = relationship()


__all__ = ["Stream"]
