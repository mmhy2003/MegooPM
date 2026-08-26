"""Pydantic schemas for dead (404) hosts.

A :class:`~app.models.dead_host.DeadHost` parks a set of domains and answers
every request with a 404 — useful to explicitly claim a domain (optionally under
TLS) without forwarding anywhere. ``domain_names`` are normalised (trimmed,
lower-cased, de-duplicated) for a stable rendered ``server_name``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.proxy_host import _normalise_domains


class DeadHostBase(BaseModel):
    """Fields shared by dead-host read/write schemas."""

    domain_names: list[str] = Field(
        min_length=1, description="Domains this host answers for (server_name)"
    )
    certificate_id: int | None = Field(
        default=None, description="Certificate for TLS termination; null serves plain :80"
    )
    ssl_forced: bool = Field(default=False, description="Redirect :80 to HTTPS")
    http2_support: bool = Field(default=False, description="Enable HTTP/2 on the TLS listener")
    hsts_enabled: bool = Field(default=False, description="Emit a Strict-Transport-Security header")
    hsts_subdomains: bool = Field(default=False, description="Include subdomains in HSTS")
    advanced_config: str = Field(
        default="", description="Raw nginx directives injected into the server block"
    )
    enabled: bool = Field(default=True, description="Disabled hosts are excluded from config")

    @field_validator("domain_names")
    @classmethod
    def _validate_domains(cls, value: list[str]) -> list[str]:
        return _normalise_domains(value)


class DeadHostCreate(DeadHostBase):
    """Payload to create a dead host."""


class DeadHostUpdate(BaseModel):
    """Partial update of a dead host; every field is optional."""

    domain_names: list[str] | None = Field(default=None, min_length=1)
    certificate_id: int | None = None
    ssl_forced: bool | None = None
    http2_support: bool | None = None
    hsts_enabled: bool | None = None
    hsts_subdomains: bool | None = None
    advanced_config: str | None = None
    enabled: bool | None = None

    @field_validator("domain_names")
    @classmethod
    def _validate_domains(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _normalise_domains(value)


class DeadHostRead(DeadHostBase):
    """Public representation of a dead host."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


__all__ = [
    "DeadHostBase",
    "DeadHostCreate",
    "DeadHostRead",
    "DeadHostUpdate",
]
