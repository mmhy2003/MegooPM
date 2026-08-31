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
    """Forwards an incoming TCP and/or UDP port to a backend.

    The target is either a single ``forward_host``/``forward_port`` or an
    upstream pool, never both — see the ``stream_target_exactly_one``
    constraint. A pool gives the stream the same weighted balancing and passive
    failover a proxy host gets.
    """

    __tablename__ = "streams"
    __table_args__ = (
        CheckConstraint("incoming_port BETWEEN 1 AND 65535", name="incoming_port_range"),
        # NULL when the stream targets a pool instead of a host:port.
        CheckConstraint(
            "forward_port IS NULL OR forward_port BETWEEN 1 AND 65535",
            name="forward_port_range",
        ),
        # Exactly one target. Enforced here as well as in the schema so a bad
        # row cannot exist even if a caller bypasses the API.
        CheckConstraint(
            "(forward_host IS NOT NULL AND forward_port IS NOT NULL AND upstream_id IS NULL)"
            " OR (forward_host IS NULL AND forward_port IS NULL AND upstream_id IS NOT NULL)",
            name="stream_target_exactly_one",
        ),
        # At least one protocol must be enabled for the stream to do anything.
        CheckConstraint("tcp_forwarding OR udp_forwarding", name="at_least_one_protocol"),
    )

    # Only one stream may claim a given incoming port.
    incoming_port: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    # Either a direct host:port target...
    forward_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    forward_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ...or a pool, which gives the stream weighted balancing and failover.
    # RESTRICT matches ProxyHost.upstream_id: a pool in use cannot be deleted.
    upstream_id: Mapped[int | None] = mapped_column(
        ForeignKey("upstreams.id", ondelete="RESTRICT"), nullable=True, index=True
    )
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
