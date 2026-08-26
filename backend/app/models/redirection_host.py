"""Redirection hosts — issue HTTP redirects for a set of domains."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import RedirectScheme
from app.models.mixins import IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.certificate import Certificate


class RedirectionHost(IdMixin, TimestampMixin, Base):
    """Redirects incoming domains to a target domain with a chosen HTTP code."""

    __tablename__ = "redirection_hosts"
    __table_args__ = (
        CheckConstraint("forward_http_code BETWEEN 300 AND 308", name="forward_http_code_range"),
    )

    domain_names: Mapped[list[str]] = mapped_column(
        ARRAY(String(255)), nullable=False, default=list, server_default="{}"
    )
    forward_scheme: Mapped[RedirectScheme] = mapped_column(
        Enum(
            RedirectScheme,
            name="redirect_scheme",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=RedirectScheme.auto,
        server_default=RedirectScheme.auto.value,
    )
    forward_domain_name: Mapped[str] = mapped_column(String(255), nullable=False)
    forward_http_code: Mapped[int] = mapped_column(
        Integer, nullable=False, default=302, server_default="302"
    )
    preserve_path: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    certificate_id: Mapped[int | None] = mapped_column(
        ForeignKey("certificates.id", ondelete="SET NULL"), nullable=True, index=True
    )

    ssl_forced: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    http2_support: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    hsts_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    hsts_subdomains: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    block_exploits: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    advanced_config: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    certificate: Mapped[Certificate | None] = relationship()


__all__ = ["RedirectionHost"]
