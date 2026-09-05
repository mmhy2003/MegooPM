"""Proxy hosts — the primary reverse-proxy entry points.

Each proxy host terminates a set of domain names and forwards matching traffic
to an :class:`~app.models.upstream.Upstream` pool, optionally guarded by an
access list and secured by a certificate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import HttpScheme, LocationTarget
from app.models.mixins import IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.access_list import AccessList
    from app.models.certificate import Certificate
    from app.models.custom_page import CustomPage
    from app.models.upstream import Upstream


class ProxyHost(IdMixin, TimestampMixin, Base):
    """A reverse-proxy host, forwarding to a pool or to a single backend.

    The target is either ``upstream_id`` or ``forward_host``/``forward_port``,
    never both — see ``host_target_exactly_one``. A pool brings weighted
    balancing and passive failover; a single backend is the simpler shape for
    one server, and cannot be skipped from the render for pool reasons because
    it has no pool.
    """

    __tablename__ = "proxy_hosts"
    __table_args__ = (
        CheckConstraint(
            "forward_port IS NULL OR forward_port BETWEEN 1 AND 65535",
            name="forward_port_range",
        ),
        CheckConstraint(
            "(forward_host IS NOT NULL AND forward_port IS NOT NULL AND upstream_id IS NULL)"
            " OR (forward_host IS NULL AND forward_port IS NULL AND upstream_id IS NOT NULL)",
            name="host_target_exactly_one",
        ),
    )

    domain_names: Mapped[list[str]] = mapped_column(
        ARRAY(String(255)), nullable=False, default=list, server_default="{}"
    )
    # Either a pool — RESTRICT: one still referenced cannot be deleted...
    upstream_id: Mapped[int | None] = mapped_column(
        ForeignKey("upstreams.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    # ...or a single backend. Exactly one; the scheme below applies to both.
    forward_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    forward_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    crowdsec_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
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
    """One ``location <path>`` block of a proxy host.

    Forwards to a pool or a single backend — the same either/or the host itself
    has — or is answered by nginx directly: the instance's default site, one
    named custom page, or a branded HTTP error. ``target`` says which, rather
    than the reader inferring it from which columns are null.
    """

    __tablename__ = "proxy_host_locations"
    __table_args__ = (
        UniqueConstraint(
            "proxy_host_id", "path", name="uq_proxy_host_locations_proxy_host_id_path"
        ),
        CheckConstraint(
            "forward_port IS NULL OR forward_port BETWEEN 1 AND 65535",
            name="forward_port_range",
        ),
        # One shape per target. Written against ``target`` rather than as a
        # count of non-null columns so a row that says "pool" but carries a
        # forward_host is rejected, not silently rendered as one of the two.
        CheckConstraint(
            "(target = 'pool' AND upstream_id IS NOT NULL AND forward_host IS NULL"
            " AND forward_port IS NULL AND custom_page_id IS NULL AND error_code IS NULL)"
            " OR (target = 'host' AND upstream_id IS NULL AND forward_host IS NOT NULL"
            " AND forward_port IS NOT NULL AND custom_page_id IS NULL AND error_code IS NULL)"
            " OR (target = 'default_site' AND upstream_id IS NULL AND forward_host IS NULL"
            " AND forward_port IS NULL AND custom_page_id IS NULL AND error_code IS NULL)"
            " OR (target = 'custom_page' AND upstream_id IS NULL AND forward_host IS NULL"
            " AND forward_port IS NULL AND custom_page_id IS NOT NULL AND error_code IS NULL)"
            " OR (target = 'error_page' AND upstream_id IS NULL AND forward_host IS NULL"
            " AND forward_port IS NULL AND custom_page_id IS NULL AND error_code IS NOT NULL)",
            name="location_target_exactly_one",
        ),
    )

    proxy_host_id: Mapped[int] = mapped_column(
        ForeignKey("proxy_hosts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    target: Mapped[LocationTarget] = mapped_column(
        Enum(
            LocationTarget,
            name="location_target",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=LocationTarget.pool,
        server_default=LocationTarget.pool.value,
    )
    # RESTRICT, like ``proxy_hosts.upstream_id``: a page in use cannot be deleted.
    custom_page_id: Mapped[int | None] = mapped_column(
        ForeignKey("custom_pages.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    # Not a foreign key to error_page: an unconfigured code has no row there and
    # still renders, so requiring one would forbid the common case.
    error_code: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # RESTRICT, like ``proxy_hosts.upstream_id``: a pool in use cannot be deleted.
    upstream_id: Mapped[int | None] = mapped_column(
        ForeignKey("upstreams.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    forward_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    forward_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    custom_page: Mapped[CustomPage] = relationship()


__all__ = ["ProxyHost", "ProxyHostLocation"]
