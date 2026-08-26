"""Dead (404) hosts — claim domains and return a 404 for any request.

Useful to explicitly park a domain on the proxy so it is terminated (and can
carry a certificate) without forwarding anywhere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.certificate import Certificate


class DeadHost(IdMixin, TimestampMixin, Base):
    """A parked domain set that always responds 404."""

    __tablename__ = "dead_hosts"

    domain_names: Mapped[list[str]] = mapped_column(
        ARRAY(String(255)), nullable=False, default=list, server_default="{}"
    )
    certificate_id: Mapped[int | None] = mapped_column(
        ForeignKey("certificates.id", ondelete="SET NULL"), nullable=True, index=True
    )

    ssl_forced: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    http2_support: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    hsts_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    hsts_subdomains: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    advanced_config: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    certificate: Mapped[Certificate | None] = relationship()


__all__ = ["DeadHost"]
