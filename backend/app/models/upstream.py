"""Upstream pools and their backends.

The defining feature of MegooPM over stock Nginx Proxy Manager: a proxy host
forwards to an :class:`Upstream` *pool* which fans out across N
:class:`UpstreamBackend` servers under a chosen load-balancing method, rather
than a single forward host/port.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import LoadBalanceMethod
from app.models.mixins import IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.proxy_host import ProxyHost


class Upstream(IdMixin, TimestampMixin, Base):
    """A named pool of backend servers with a load-balancing policy."""

    __tablename__ = "upstreams"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    lb_method: Mapped[LoadBalanceMethod] = mapped_column(
        Enum(
            LoadBalanceMethod,
            name="load_balance_method",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=LoadBalanceMethod.round_robin,
        server_default=LoadBalanceMethod.round_robin.value,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    backends: Mapped[list[UpstreamBackend]] = relationship(
        back_populates="upstream",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    # A pool may be shared by several proxy hosts; deleting a referenced pool is
    # blocked (RESTRICT) — see ``ProxyHost.upstream_id``.
    proxy_hosts: Mapped[list[ProxyHost]] = relationship(back_populates="upstream")


class UpstreamBackend(IdMixin, TimestampMixin, Base):
    """A single ``server`` entry within an :class:`Upstream` pool."""

    __tablename__ = "upstream_backends"
    __table_args__ = (
        UniqueConstraint("upstream_id", "host", "port", name="upstream_backends_host_port"),
        CheckConstraint("port BETWEEN 1 AND 65535", name="port_range"),
        CheckConstraint("weight >= 0", name="weight_non_negative"),
        CheckConstraint("max_fails >= 0", name="max_fails_non_negative"),
        CheckConstraint("fail_timeout_seconds >= 0", name="fail_timeout_non_negative"),
    )

    upstream_id: Mapped[int] = mapped_column(
        ForeignKey("upstreams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    max_fails: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    fail_timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default="10"
    )
    # ``backup`` servers only receive traffic when the primaries are down.
    backup: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # ``down`` administratively removes the server from rotation.
    down: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    upstream: Mapped[Upstream] = relationship(back_populates="backends")


__all__ = ["Upstream", "UpstreamBackend"]
