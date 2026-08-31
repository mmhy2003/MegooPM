"""Pydantic schemas for upstream pools and their backends.

An :class:`~app.models.upstream.Upstream` is a named, load-balanced pool of
backend servers. A proxy host forwards to a pool rather than a single origin —
MegooPM's headline capability over stock Nginx Proxy Manager.

Backends may be supplied inline when creating a pool (:class:`UpstreamCreate`)
or managed individually through the ``/upstreams/{id}/backends`` sub-resource.
Field constraints mirror the database ``CHECK`` constraints so invalid input is
rejected at the API boundary with a 422 rather than surfacing as a 500 from the
database.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import LoadBalanceMethod, UpstreamContext


class BackendBase(BaseModel):
    """Fields describing a single ``server`` line in an ``upstream`` block."""

    host: str = Field(min_length=1, max_length=255, description="Backend host or IP")
    port: int = Field(ge=1, le=65535, description="Backend TCP port")
    weight: int = Field(default=1, ge=0, description="Relative load-balancing weight")
    max_fails: int = Field(
        default=1, ge=0, description="Failed attempts before the backend is marked down"
    )
    fail_timeout_seconds: int = Field(
        default=10, ge=0, description="Window/penalty for max_fails, in seconds"
    )
    backup: bool = Field(default=False, description="Only used when primaries are down")
    down: bool = Field(default=False, description="Administratively removed from rotation")
    enabled: bool = Field(default=True, description="Excluded from the rendered pool when false")

    @field_validator("host")
    @classmethod
    def _strip_host(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped or any(c.isspace() for c in stripped):
            raise ValueError("host must be a non-empty value without whitespace")
        return stripped


class BackendCreate(BackendBase):
    """Payload to add a backend to a pool."""


class BackendUpdate(BaseModel):
    """Partial update of a backend; every field is optional."""

    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    weight: int | None = Field(default=None, ge=0)
    max_fails: int | None = Field(default=None, ge=0)
    fail_timeout_seconds: int | None = Field(default=None, ge=0)
    backup: bool | None = None
    down: bool | None = None
    enabled: bool | None = None

    @field_validator("host")
    @classmethod
    def _strip_host(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped or any(c.isspace() for c in stripped):
            raise ValueError("host must be a non-empty value without whitespace")
        return stripped


class BackendRead(BackendBase):
    """Public representation of a backend."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    upstream_id: int
    created_at: datetime
    updated_at: datetime


class UpstreamBase(BaseModel):
    """Fields shared by upstream read/write schemas."""

    name: str = Field(min_length=1, max_length=255, description="Human-readable pool name")
    description: str = Field(default="", description="Optional free-text description")
    lb_method: LoadBalanceMethod = Field(
        default=LoadBalanceMethod.round_robin, description="nginx load-balancing strategy"
    )
    context: UpstreamContext = Field(
        default=UpstreamContext.http,
        description=(
            "Where the pool may be attached: http (proxy hosts), stream "
            "(TCP/UDP), or both. ip_hash is only valid for http."
        ),
    )
    enabled: bool = Field(default=True, description="Disabled pools are excluded from config")


class UpstreamCreate(UpstreamBase):
    """Payload to create a pool, optionally seeding its backends inline."""

    backends: list[BackendCreate] = Field(default_factory=list)


class UpstreamUpdate(BaseModel):
    """Partial update of a pool's own attributes (not its backends)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    lb_method: LoadBalanceMethod | None = None
    context: UpstreamContext | None = None
    enabled: bool | None = None


class UpstreamRead(UpstreamBase):
    """Public representation of a pool, including its backends."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    backends: list[BackendRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


__all__ = [
    "BackendBase",
    "BackendCreate",
    "BackendRead",
    "BackendUpdate",
    "UpstreamBase",
    "UpstreamCreate",
    "UpstreamRead",
    "UpstreamUpdate",
]
