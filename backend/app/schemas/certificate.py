"""Schemas for the certificate management endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import CertificateProvider, CertificateStatus
from app.services.certs.acme_client import ChallengeType


class CertificateRead(BaseModel):
    """A certificate as returned by the API (never includes key material)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    provider: CertificateProvider
    status: CertificateStatus
    domain_names: list[str]
    expires_on: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CustomCertificateCreate(BaseModel):
    """Upload of a custom certificate: PEM cert + private key (+ optional chain)."""

    name: str = Field(min_length=1, max_length=255)
    certificate_pem: str = Field(description="Leaf certificate in PEM format")
    private_key_pem: str = Field(description="Matching private key in PEM format")
    chain_pem: str | None = Field(
        default=None, description="Intermediate chain in PEM format (optional)"
    )


class LetsEncryptCertificateCreate(BaseModel):
    """Request to issue a Let's Encrypt certificate for a set of domains."""

    name: str = Field(min_length=1, max_length=255)
    domain_names: list[str] = Field(min_length=1)
    challenge: str = Field(
        default=ChallengeType.HTTP_01,
        description="ACME challenge type: 'http-01' (default) or 'dns-01'",
    )
    account_email: str | None = Field(
        default=None, description="Contact email for the ACME account (optional)"
    )


class CertificateIssued(BaseModel):
    """Response for an issuance request: the pending cert plus its tracking task."""

    certificate: CertificateRead
    task_id: str
    task_status: str


__all__ = [
    "CertificateIssued",
    "CertificateRead",
    "CustomCertificateCreate",
    "LetsEncryptCertificateCreate",
]
