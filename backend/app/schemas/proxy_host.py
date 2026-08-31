"""Pydantic schemas for proxy hosts.

A :class:`~app.models.proxy_host.ProxyHost` terminates a set of domain names and
forwards matching traffic to an upstream pool (``upstream_id``) or to a single
``forward_host``/``forward_port`` — exactly one. These schemas
form the versioned API contract the frontend consumes; ``domain_names`` are
normalised (trimmed, lower-cased, de-duplicated) so the rendered ``server_name``
is stable regardless of how the client capitalises them.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


_LOCATION_FORBIDDEN = frozenset('{};"')


def _validate_location_path(value: str) -> str:
    """Enforce the spec's path rules; the value is embedded in a ``location`` directive."""
    path = value.strip()
    if not path.startswith("/"):
        raise ValueError("location path must start with '/'")
    if path == "/":
        raise ValueError("'/' is the host's root route; use a sub-path such as /api/")
    if any(ch.isspace() or ch in _LOCATION_FORBIDDEN for ch in path):
        raise ValueError('location path must not contain whitespace or any of { } ; "')
    if len(path) > 255:
        raise ValueError("location path must be at most 255 characters")
    return path


def _unique_location_paths(value: list[ProxyHostLocationIn]) -> list[ProxyHostLocationIn]:
    seen: set[str] = set()
    for loc in value:
        if loc.path in seen:
            raise ValueError(f"duplicate location path: {loc.path!r}")
        seen.add(loc.path)
    return value


class ProxyHostLocationIn(BaseModel):
    """One extra ``location <path>`` route of a proxy host."""

    path: str = Field(description="URL prefix, e.g. /api/ (the root '/' is the host itself)")
    upstream_id: int = Field(description="Pool this prefix forwards to")
    forward_scheme: HttpScheme = Field(
        default=HttpScheme.http, description="Scheme used to reach the pool (http/https)"
    )

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return _validate_location_path(value)


class ProxyHostLocationRead(ProxyHostLocationIn):
    """Stored location (adds the row id)."""

    model_config = ConfigDict(from_attributes=True)

    id: int


class ProxyHostBase(BaseModel):
    """Fields shared by proxy-host read/write schemas."""

    domain_names: list[str] = Field(
        min_length=1, description="Domains this host answers for (server_name)"
    )
    upstream_id: int | None = Field(
        default=None, description="Pool to forward matched traffic to; null when using a host"
    )
    forward_host: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Single backend host; null when forwarding to a pool",
    )
    forward_port: int | None = Field(
        default=None,
        ge=1,
        le=65535,
        description="Single backend port; null when forwarding to a pool",
    )
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
    locations: list[ProxyHostLocationIn] = Field(
        default_factory=list,
        description="Extra path-prefixed routes to other pools (rendered as location ^~ <path>)",
    )

    @model_validator(mode="after")
    def _require_exactly_one_target(self) -> ProxyHostBase:
        """Mirrors the DB constraint so the API answers 422, not a 500.

        A host without a port is deliberately not a target: without this it
        would slip through and fail at the constraint instead, which reports the
        problem far from the field that caused it.
        """
        host_target = self.forward_host is not None and self.forward_port is not None
        if host_target == (self.upstream_id is not None):
            raise ValueError("Set either a forward host and port, or an upstream pool.")
        return self

    @field_validator("domain_names")
    @classmethod
    def _validate_domains(cls, value: list[str]) -> list[str]:
        return _normalise_domains(value)

    @field_validator("locations")
    @classmethod
    def _validate_locations(cls, value: list[ProxyHostLocationIn]) -> list[ProxyHostLocationIn]:
        return _unique_location_paths(value)


class ProxyHostCreate(ProxyHostBase):
    """Payload to create a proxy host."""


class ProxyHostUpdate(BaseModel):
    """Partial update of a proxy host; every field is optional."""

    domain_names: list[str] | None = Field(default=None, min_length=1)
    upstream_id: int | None = None
    forward_host: str | None = Field(default=None, min_length=1, max_length=255)
    forward_port: int | None = Field(default=None, ge=1, le=65535)
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
    locations: list[ProxyHostLocationIn] | None = None

    @field_validator("domain_names")
    @classmethod
    def _validate_domains(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _normalise_domains(value)

    @field_validator("locations")
    @classmethod
    def _validate_locations(
        cls, value: list[ProxyHostLocationIn] | None
    ) -> list[ProxyHostLocationIn] | None:
        if value is None:
            return None
        return _unique_location_paths(value)


class ProxyHostRead(ProxyHostBase):
    """Public representation of a proxy host."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
    locations: list[ProxyHostLocationRead] = Field(default_factory=list)


__all__ = [
    "ProxyHostBase",
    "ProxyHostCreate",
    "ProxyHostLocationIn",
    "ProxyHostLocationRead",
    "ProxyHostRead",
    "ProxyHostUpdate",
]
