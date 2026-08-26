"""Pydantic schemas for proxy hosts.

A :class:`~app.models.proxy_host.ProxyHost` terminates a set of domain names and
forwards matching traffic to an upstream pool (``upstream_id``). These schemas
form the versioned API contract the frontend consumes; ``domain_names`` are
normalised (trimmed, lower-cased, de-duplicated) so the rendered ``server_name``
is stable regardless of how the client capitalises them.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import HttpScheme

# A lenient hostname matcher that also accepts a leading wildcard label
# (``*.example.com``). Full RFC compliance is not the goal — we reject obvious
# junk (spaces, empty labels, illegal characters) and let nginx be the final
# authority via ``nginx -t`` when the config is applied.
_DOMAIN_RE = re.compile(
    r"^(?:\*\.)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


def _normalise_domains(value: list[str]) -> list[str]:
    """Trim, lower-case, validate and de-duplicate a list of domain names."""
    seen: set[str] = set()
    result: list[str] = []
    for raw in value:
        name = raw.strip().lower()
        if not name:
            raise ValueError("domain names must not be empty")
        if not _DOMAIN_RE.match(name):
            raise ValueError(f"invalid domain name: {raw!r}")
        if name not in seen:
            seen.add(name)
            result.append(name)
    if not result:
        raise ValueError("at least one domain name is required")
    return result


class ProxyHostBase(BaseModel):
    """Fields shared by proxy-host read/write schemas."""

    domain_names: list[str] = Field(
        min_length=1, description="Domains this host answers for (server_name)"
    )
    upstream_id: int = Field(description="The upstream pool to forward matched traffic to")
    forward_scheme: HttpScheme = Field(
        default=HttpScheme.http, description="Scheme used to reach the upstream (http/https)"
    )
    certificate_id: int | None = Field(
        default=None, description="Certificate for TLS termination; null serves plain :80"
    )
    access_list_id: int | None = Field(
        default=None, description="Optional access list guarding this host"
    )
    ssl_forced: bool = Field(default=False, description="Redirect :80 to HTTPS")
    http2_support: bool = Field(default=False, description="Enable HTTP/2 on the TLS listener")
    hsts_enabled: bool = Field(default=False, description="Emit a Strict-Transport-Security header")
    hsts_subdomains: bool = Field(default=False, description="Include subdomains in HSTS")
    caching_enabled: bool = Field(default=False, description="Cache static assets")
    block_exploits: bool = Field(default=False, description="Block common exploit probes")
    allow_websocket_upgrade: bool = Field(
        default=False, description="Pass Upgrade/Connection headers for websockets"
    )
    crowdsec_enabled: bool = Field(
        default=False, description="Enforce the CrowdSec nginx bouncer on this host"
    )
    crowdsec_appsec_enabled: bool = Field(
        default=False,
        description="Route requests through CrowdSec inline AppSec/WAF (needs crowdsec_enabled)",
    )
    advanced_config: str = Field(
        default="", description="Raw nginx directives injected into the server block"
    )
    enabled: bool = Field(default=True, description="Disabled hosts are excluded from config")

    @field_validator("domain_names")
    @classmethod
    def _validate_domains(cls, value: list[str]) -> list[str]:
        return _normalise_domains(value)


class ProxyHostCreate(ProxyHostBase):
    """Payload to create a proxy host."""


class ProxyHostUpdate(BaseModel):
    """Partial update of a proxy host; every field is optional."""

    domain_names: list[str] | None = Field(default=None, min_length=1)
    upstream_id: int | None = None
    forward_scheme: HttpScheme | None = None
    certificate_id: int | None = None
    access_list_id: int | None = None
    ssl_forced: bool | None = None
    http2_support: bool | None = None
    hsts_enabled: bool | None = None
    hsts_subdomains: bool | None = None
    caching_enabled: bool | None = None
    block_exploits: bool | None = None
    allow_websocket_upgrade: bool | None = None
    crowdsec_enabled: bool | None = None
    crowdsec_appsec_enabled: bool | None = None
    advanced_config: str | None = None
    enabled: bool | None = None

    @field_validator("domain_names")
    @classmethod
    def _validate_domains(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _normalise_domains(value)


class ProxyHostRead(ProxyHostBase):
    """Public representation of a proxy host."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


__all__ = [
    "ProxyHostBase",
    "ProxyHostCreate",
    "ProxyHostRead",
    "ProxyHostUpdate",
]
