"""Proxy hosts — the primary reverse-proxy entry points.

Each proxy host terminates a set of domain names and forwards matching traffic
to an :class:`~app.models.upstream.Upstream` pool, optionally guarded by an
access list and secured by a certificate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import HttpScheme
from app.models.mixins import IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.access_list import AccessList
    from app.models.certificate import Certificate
    from app.models.upstream import Upstream


class ProxyHost(IdMixin, TimestampMixin, Base):
    """A reverse-proxy host that fans traffic out to an upstream pool."""

    __tablename__ = "proxy_hosts"

    domain_names: Mapped[list[str]] = mapped_column(
        ARRAY(String(255)), nullable=False, default=list, server_default="{}"
    )
    # Every proxy host forwards to exactly one pool. RESTRICT: a pool that is
    # still referenced cannot be deleted.
    upstream_id: Mapped[int] = mapped_column(
        ForeignKey("upstreams.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    forward_scheme: Mapped[HttpScheme] = mapped_column(
        Enum(
            HttpScheme,
            name="http_scheme",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=HttpScheme.http,
        server_default=HttpScheme.http.value,
    )
    certificate_id: Mapped[int | None] = mapped_column(
        ForeignKey("certificates.id", ondelete="SET NULL"), nullable=True, index=True
    )
    access_list_id: Mapped[int | None] = mapped_column(
        ForeignKey("access_lists.id", ondelete="SET NULL"), nullable=True, index=True
    )

    ssl_forced: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    http2_support: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    hsts_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    hsts_subdomains: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    caching_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    block_exploits: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    allow_websocket_upgrade: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # CrowdSec (MEG-22). ``crowdsec_enabled`` wires the nginx bouncer into this
    # host so banned IPs are refused at the edge; ``crowdsec_appsec_enabled``
    # additionally routes requests through the inline AppSec/WAF component.
    # AppSec has no effect unless the bouncer is enabled.
    crowdsec_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    crowdsec_appsec_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    advanced_config: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    upstream: Mapped[Upstream] = relationship(back_populates="proxy_hosts")
    access_list: Mapped[AccessList | None] = relationship(back_populates="proxy_hosts")
    certificate: Mapped[Certificate | None] = relationship()
    # Extra path-prefixed routes (``location ^~ /path``) to other pools. The
    # root ``/`` route is ``upstream_id``/``forward_scheme`` above.
    locations: Mapped[list[ProxyHostLocation]] = relationship(
        back_populates="proxy_host",
        cascade="all, delete-orphan",
        order_by="ProxyHostLocation.path",
    )


class ProxyHostLocation(IdMixin, TimestampMixin, Base):
    """One ``location <path>`` block of a proxy host forwarding to a pool."""

    __tablename__ = "proxy_host_locations"
    __table_args__ = (
        UniqueConstraint(
            "proxy_host_id", "path", name="uq_proxy_host_locations_proxy_host_id_path"
        ),
    )

    proxy_host_id: Mapped[int] = mapped_column(
        ForeignKey("proxy_hosts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    # RESTRICT, like ``proxy_hosts.upstream_id``: a pool in use cannot be deleted.
    upstream_id: Mapped[int] = mapped_column(
        ForeignKey("upstreams.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    forward_scheme: Mapped[HttpScheme] = mapped_column(
        Enum(
            HttpScheme,
            name="http_scheme",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=HttpScheme.http,
        server_default=HttpScheme.http.value,
    )

    proxy_host: Mapped[ProxyHost] = relationship(back_populates="locations")
    upstream: Mapped[Upstream] = relationship()


__all__ = ["ProxyHost", "ProxyHostLocation"]
