"""Pydantic schemas for streams (raw TCP/UDP port forwards).

A :class:`~app.models.stream.Stream` forwards an incoming port to a backend
``host:port`` over TCP, UDP, or both. These schemas form the versioned API
contract the frontend consumes. At least one protocol must be enabled (mirroring
the DB check constraint) so a stream always does something.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

_MIN_PORT = 1
_MAX_PORT = 65535


class StreamBase(BaseModel):
    """Fields shared by stream read/write schemas."""

    incoming_port: int = Field(
        ge=_MIN_PORT, le=_MAX_PORT, description="Port nginx listens on for this stream"
    )
    forward_host: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Backend host traffic is forwarded to; null when using a pool",
    )
    forward_port: int | None = Field(
        default=None,
        ge=_MIN_PORT,
        le=_MAX_PORT,
        description="Backend port traffic is forwarded to; null when using a pool",
    )
    upstream_id: int | None = Field(
        default=None,
        description=(
            "Upstream pool to forward to, for weighted balancing and failover. "
            "Mutually exclusive with forward_host/forward_port."
        ),
    )
    tcp_forwarding: bool = Field(default=True, description="Forward TCP on the incoming port")
    udp_forwarding: bool = Field(default=False, description="Forward UDP on the incoming port")
    certificate_id: int | None = Field(
        default=None, description="Certificate to terminate TLS on the TCP listener; null = plain"
    )
    enabled: bool = Field(default=True, description="Disabled streams are excluded from config")

    @model_validator(mode="after")
    def _require_a_protocol(self) -> StreamBase:
        if not (self.tcp_forwarding or self.udp_forwarding):
            raise ValueError("at least one of tcp_forwarding or udp_forwarding must be enabled")
        return self

    @model_validator(mode="after")
    def _require_exactly_one_target(self) -> StreamBase:
        """Mirrors the DB constraint so the API answers 422 rather than 500.

        A host without a port is not a target: treating it as one would let a
        half-specified stream reach the database and fail there instead.
        """
        host_target = self.forward_host is not None and self.forward_port is not None
        if host_target == (self.upstream_id is not None):
            raise ValueError("Set either a forward host and port, or an upstream pool.")
        return self


class StreamCreate(StreamBase):
    """Payload to create a stream."""


class StreamUpdate(BaseModel):
    """Partial update of a stream; every field is optional.

    The "at least one protocol" rule can only be enforced against the merged
    result, so it is checked in the service/DB layer rather than here (a PATCH
    that flips only one flag has no view of the other).
    """

    incoming_port: int | None = Field(default=None, ge=_MIN_PORT, le=_MAX_PORT)
    forward_host: str | None = Field(default=None, min_length=1, max_length=255)
    forward_port: int | None = Field(default=None, ge=_MIN_PORT, le=_MAX_PORT)
    upstream_id: int | None = None
    tcp_forwarding: bool | None = None
    udp_forwarding: bool | None = None
    certificate_id: int | None = None
    enabled: bool | None = None


class StreamRead(StreamBase):
    """Public representation of a stream."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


__all__ = [
    "StreamBase",
    "StreamCreate",
    "StreamRead",
    "StreamUpdate",
]
