"""Pydantic schemas for redirection hosts.

A :class:`~app.models.redirection_host.RedirectionHost` claims a set of domains
and answers every request with an HTTP redirect (301/302/…) to a target domain.
These schemas form the versioned API contract the frontend consumes;
``domain_names`` are normalised (trimmed, lower-cased, de-duplicated) so the
rendered ``server_name`` is stable regardless of client capitalisation.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import RedirectScheme
from app.schemas.proxy_host import _normalise_domains

# nginx can only `return` the 3xx redirect codes; mirror the DB check constraint.
_MIN_REDIRECT_CODE = 300
_MAX_REDIRECT_CODE = 308


class RedirectionHostBase(BaseModel):
    """Fields shared by redirection-host read/write schemas."""

    domain_names: list[str] = Field(
        min_length=1, description="Domains this host answers for (server_name)"
    )
    forward_domain_name: str = Field(
        min_length=1, max_length=255, description="Domain requests are redirected to"
    )
    forward_scheme: RedirectScheme = Field(
        default=RedirectScheme.auto,
        description="Target scheme; 'auto' keeps the incoming request's scheme",
    )
    forward_http_code: int = Field(
        default=302,
        ge=_MIN_REDIRECT_CODE,
        le=_MAX_REDIRECT_CODE,
        description="HTTP redirect status code (300–308)",
    )
    preserve_path: bool = Field(
        default=True, description="Append the original request URI to the target"
    )
    certificate_id: int | None = Field(
        default=None, description="Certificate for TLS termination; null serves plain :80"
    )
    ssl_forced: bool = Field(default=False, description="Redirect :80 to HTTPS")
    http2_support: bool = Field(default=False, description="Enable HTTP/2 on the TLS listener")
    hsts_enabled: bool = Field(default=False, description="Emit a Strict-Transport-Security header")
    hsts_subdomains: bool = Field(default=False, description="Include subdomains in HSTS")
    block_exploits: bool = Field(default=False, description="Block common exploit probes")
    advanced_config: str = Field(
        default="", description="Raw nginx directives injected into the server block"
    )
    enabled: bool = Field(default=True, description="Disabled hosts are excluded from config")

    @field_validator("domain_names")
    @classmethod
    def _validate_domains(cls, value: list[str]) -> list[str]:
        return _normalise_domains(value)

    @field_validator("forward_domain_name")
    @classmethod
    def _validate_forward_domain(cls, value: str) -> str:
        # Reuse the domain normaliser on a single-element list so the target is
        # held to the same hostname rules as the served domains.
        return _normalise_domains([value])[0]


class RedirectionHostCreate(RedirectionHostBase):
    """Payload to create a redirection host."""


class RedirectionHostUpdate(BaseModel):
    """Partial update of a redirection host; every field is optional."""

    domain_names: list[str] | None = Field(default=None, min_length=1)
    forward_domain_name: str | None = Field(default=None, min_length=1, max_length=255)
    forward_scheme: RedirectScheme | None = None
    forward_http_code: int | None = Field(
        default=None, ge=_MIN_REDIRECT_CODE, le=_MAX_REDIRECT_CODE
    )
    preserve_path: bool | None = None
    certificate_id: int | None = None
    ssl_forced: bool | None = None
    http2_support: bool | None = None
    hsts_enabled: bool | None = None
    hsts_subdomains: bool | None = None
    block_exploits: bool | None = None
    advanced_config: str | None = None
    enabled: bool | None = None

    @field_validator("domain_names")
    @classmethod
    def _validate_domains(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _normalise_domains(value)

    @field_validator("forward_domain_name")
    @classmethod
    def _validate_forward_domain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalise_domains([value])[0]


class RedirectionHostRead(RedirectionHostBase):
    """Public representation of a redirection host."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


__all__ = [
    "RedirectionHostBase",
    "RedirectionHostCreate",
    "RedirectionHostRead",
    "RedirectionHostUpdate",
]
