"""Immutable value objects describing the *desired* nginx configuration.

These DTOs are the boundary between the database and the config generator: the
loader (:mod:`app.services.nginx.loader`) maps ORM rows onto them, and the
renderer (:mod:`app.services.nginx.renderer`) turns them into ``.conf`` text.

Keeping the renderer's input as plain, hashable dataclasses — rather than live
ORM instances — makes generation a pure function of explicit data. That is what
lets the rendering logic be unit-tested exhaustively without a database, and it
guarantees the output is a deterministic function of the inputs (a hard
requirement for idempotency).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class BackendSpec:
    """A single ``server`` line inside an ``upstream`` block."""

    host: str
    port: int
    weight: int = 1
    max_fails: int = 1
    fail_timeout_seconds: int = 10
    backup: bool = False
    down: bool = False


@dataclass(frozen=True, slots=True)
class UpstreamSpec:
    """A load-balanced pool rendered as an ``upstream {}`` block."""

    id: int
    name: str
    # nginx directive value: round_robin | least_conn | ip_hash | hash | random.
    lb_method: str = "round_robin"
    backends: tuple[BackendSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class CertificateSpec:
    """The bits of a certificate the server block needs to reference on disk."""

    id: int
    # Conventional on-disk locations under the shared certs volume. Certificate
    # provisioning (a separate ticket) is responsible for placing the files;
    # the generator only references these paths.
    fullchain_path: str
    privkey_path: str


@dataclass(frozen=True, slots=True)
class ProxyHostSpec:
    """A reverse-proxy vhost rendered as a ``server {}`` block."""

    id: int
    domain_names: tuple[str, ...]
    upstream_id: int
    forward_scheme: str = "http"
    certificate: CertificateSpec | None = None
    ssl_forced: bool = False
    http2_support: bool = False
    hsts_enabled: bool = False
    hsts_subdomains: bool = False
    caching_enabled: bool = False
    block_exploits: bool = False
    allow_websocket_upgrade: bool = False
    advanced_config: str = ""


@dataclass(frozen=True, slots=True)
class DesiredState:
    """The complete set of managed objects a render pass should emit.

    ``upstreams`` should contain exactly the pools referenced by ``proxy_hosts``;
    the loader guarantees this so no orphan ``upstream`` blocks are written.
    """

    proxy_hosts: tuple[ProxyHostSpec, ...] = field(default_factory=tuple)
    upstreams: tuple[UpstreamSpec, ...] = field(default_factory=tuple)


__all__ = [
    "BackendSpec",
    "UpstreamSpec",
    "CertificateSpec",
    "ProxyHostSpec",
    "DesiredState",
]
