"""Schemas for the DNS provider catalog and saved DNS credentials (DNS-01)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DnsProviderFieldRead(BaseModel):
    name: str
    label: str
    help: str
    secret: bool


class DnsProviderInfoRead(BaseModel):
    id: str
    label: str
    description: str
    fields: list[DnsProviderFieldRead]


class CertificateRef(BaseModel):
    id: int
    name: str


class DnsCredentialRead(BaseModel):
    """A saved credential set. Secret values are never returned — only their names."""

    id: int
    name: str
    provider: str
    provider_label: str
    options: dict[str, str]
    secret_fields: list[str]
    in_use_by: list[CertificateRef]
    created_at: datetime
    updated_at: datetime


class DnsCredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    provider: str = Field(min_length=1, max_length=64, description="dns-lexicon provider id")
    options: dict[str, str] = Field(
        default_factory=dict,
        description="Provider fields (see /dns-providers); secrets included",
    )


class DnsCredentialUpdate(BaseModel):
    """Rename and/or replace options. A blank or omitted secret keeps its stored value."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    options: dict[str, str] | None = None


class DnsCredentialVerify(BaseModel):
    domain: str = Field(min_length=1, max_length=253, description="A domain inside the zone")


class DnsCredentialVerified(BaseModel):
    ok: bool = True


__all__ = [
    "CertificateRef",
    "DnsCredentialCreate",
    "DnsCredentialRead",
    "DnsCredentialUpdate",
    "DnsCredentialVerified",
    "DnsCredentialVerify",
    "DnsProviderFieldRead",
    "DnsProviderInfoRead",
]
