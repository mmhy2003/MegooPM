"""TLS certificates referenced by proxy, redirection, dead, and stream hosts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Enum, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import CertificateProvider, CertificateStatus
from app.models.mixins import IdMixin, TimestampMixin


class Certificate(IdMixin, TimestampMixin, Base):
    """A managed or uploaded TLS certificate.

    ``meta`` holds provider-specific data — e.g. the Let's Encrypt account email
    and DNS-challenge config, or the on-disk paths of an uploaded custom cert.
    Private key material is never stored in this column.
    """

    __tablename__ = "certificates"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[CertificateProvider] = mapped_column(
        Enum(
            CertificateProvider,
            name="certificate_provider",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    status: Mapped[CertificateStatus] = mapped_column(
        Enum(
            CertificateStatus,
            name="certificate_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=CertificateStatus.pending,
        server_default=CertificateStatus.pending.value,
    )
    domain_names: Mapped[list[str]] = mapped_column(
        ARRAY(String(255)), nullable=False, default=list, server_default="{}"
    )
    expires_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    @property
    def challenge(self) -> str | None:
        """ACME challenge type for Let's Encrypt certificates (from ``meta``)."""
        if self.provider != CertificateProvider.letsencrypt:
            return None
        return (self.meta or {}).get("challenge")

    @property
    def dns_provider(self) -> str | None:
        """dns-lexicon provider id used for DNS-01 (from ``meta``), else ``None``."""
        return (self.meta or {}).get("dns_provider")


__all__ = ["Certificate"]
