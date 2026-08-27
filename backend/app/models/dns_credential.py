"""Reusable DNS provider credentials for DNS-01 challenges.

One row per saved credential set (e.g. "Cloudflare — prod token"). The
provider's non-secret options (zone ids, server URLs) are stored in the clear
in ``options`` so the UI can show them; the secret fields are one Fernet token
(``secrets_enc``, see :mod:`app.core.crypto`) wrapping a JSON object.
Certificates reference a row through ``Certificate.meta["dns_credential_id"]``
— no foreign key on purpose, so certificate history survives a deleted
credential.
"""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin


class DnsProviderCredential(IdMixin, TimestampMixin, Base):
    """Saved credentials for one dns-lexicon provider (secrets encrypted)."""

    __tablename__ = "dns_provider_credentials"

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # dns-lexicon provider id, validated against the generated catalog on write.
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    # Non-secret provider options (e.g. zone_id, pdns_server) — plaintext JSON.
    options: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    # Fernet-encrypted JSON object of the secret options (auth_token, ...).
    secrets_enc: Mapped[str] = mapped_column(Text, nullable=False)


__all__ = ["DnsProviderCredential"]
