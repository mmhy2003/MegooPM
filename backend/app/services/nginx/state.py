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
    # Identifies the *material* currently at those paths. Renewal rewrites the
    # files in place, leaving the paths — and therefore the rendered config —
    # byte-identical, so the engine's idempotency check saw "no change" and
    # skipped the reload: every node kept serving the old certificate from
    # memory until some unrelated edit happened to trigger one. Rendering this
    # into the server block makes a renewal a real config change, so the
    # existing apply → version-bump → propagate path carries it to every node
    # with no special casing. Derived from database columns, never from the
    # files, so all nodes render identical text without reading the shared
    # mount.
    fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class AuthUserSpec:
    """One ``username:hash`` pair destined for a host's htpasswd file."""

    username: str
    # An nginx-native ``$apr1$`` (salted MD5) hash — see app.services.htpasswd.
    password_hash: str


@dataclass(frozen=True, slots=True)
class ClientRuleSpec:
    """An ``allow``/``deny`` directive for an IP, CIDR, or the literal ``all``."""

    directive: str  # "allow" | "deny"
    address: str


@dataclass(frozen=True, slots=True)
class AccessListSpec:
    """An access list rendered as ``auth_basic`` + ``allow``/``deny`` on a host."""

    id: int
    name: str
    # Satisfy ANY gate (auth OR ip) vs. ALL gates. Only meaningful when both a
    # basic-auth gate and at least one client rule are present.
    satisfy_any: bool = False
    # Forward the Authorization header to the upstream instead of stripping it.
    pass_auth: bool = False
    auth_users: tuple[AuthUserSpec, ...] = ()
    client_rules: tuple[ClientRuleSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class LocationSpec:
    """An extra ``location ^~ <path>`` route of a proxy host.

    Forwards to a pool or a single backend, exactly as the host itself does.
    """

    path: str
    upstream_id: int | None = None
    forward_host: str | None = None
    forward_port: int | None = None
    forward_scheme: str = "http"


@dataclass(frozen=True, slots=True)
class ProxyHostSpec:
    """A reverse-proxy vhost rendered as a ``server {}`` block.

    Forwards to either an upstream pool or a single ``forward_host``/
    ``forward_port`` — exactly one, which a DB check constraint guarantees.
    ``forward_scheme`` applies to both.
    """

    id: int
    domain_names: tuple[str, ...]
    upstream_id: int | None = None
    forward_host: str | None = None
    forward_port: int | None = None
    forward_scheme: str = "http"
    certificate: CertificateSpec | None = None
    access_list: AccessListSpec | None = None
    ssl_forced: bool = False
    http2_support: bool = False
    hsts_enabled: bool = False
    hsts_subdomains: bool = False
    caching_enabled: bool = False
    block_exploits: bool = False
    allow_websocket_upgrade: bool = False
    # CrowdSec (MEG-22): edge bouncer + optional inline AppSec/WAF for this host.
    crowdsec_enabled: bool = False
    crowdsec_appsec_enabled: bool = False
    advanced_config: str = ""
    # Extra path-prefixed routes; the root ``/`` is ``upstream_id``/``forward_scheme``.
    locations: tuple[LocationSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class RedirectionHostSpec:
    """A redirect-only vhost rendered as an HTTP ``server {}`` block."""

    id: int
    domain_names: tuple[str, ...]
    forward_domain_name: str
    # 300–308; the exact status the ``return`` directive emits.
    forward_http_code: int = 302
    # auto | http | https — ``auto`` preserves the incoming request scheme.
    forward_scheme: str = "auto"
    # Carry the original request URI/path onto the target when True.
    preserve_path: bool = True
    certificate: CertificateSpec | None = None
    ssl_forced: bool = False
    http2_support: bool = False
    hsts_enabled: bool = False
    hsts_subdomains: bool = False
    block_exploits: bool = False
    advanced_config: str = ""


@dataclass(frozen=True, slots=True)
class DeadHostSpec:
    """A parked ``server {}`` block that always answers 404."""

    id: int
    domain_names: tuple[str, ...]
    certificate: CertificateSpec | None = None
    ssl_forced: bool = False
    http2_support: bool = False
    hsts_enabled: bool = False
    hsts_subdomains: bool = False
    advanced_config: str = ""


@dataclass(frozen=True, slots=True)
class StreamSpec:
    """A raw TCP/UDP forward rendered inside the top-level ``stream {}`` context.

    A single stream may forward TCP, UDP, or both from ``incoming_port`` to
    either ``forward_host:forward_port`` or an upstream pool — exactly one, which
    a DB check constraint guarantees. At least one protocol is always enabled
    (another constraint). When a certificate is present, the TCP listener
    terminates TLS (``listen ... ssl``); UDP cannot.
    """

    id: int
    incoming_port: int
    forward_host: str | None = None
    forward_port: int | None = None
    upstream_id: int | None = None
    tcp_forwarding: bool = True
    udp_forwarding: bool = False
    certificate: CertificateSpec | None = None


@dataclass(frozen=True, slots=True)
class DefaultSiteSpec:
    """What nginx answers for a request matching no configured host.

    ``html`` is already resolved: the loader reads the referenced custom page's
    document and puts it here, so the renderer never reaches into the database
    and the whole mode matrix stays unit-testable without one.
    """

    # One of DefaultSiteMode's values, as a plain string — specs stay free of
    # ORM enums so they remain trivially constructible in tests.
    mode: str
    redirect_url: str = ""
    html: str = ""


@dataclass(frozen=True, slots=True)
class DefaultTlsSpec:
    """The default site served over TLS for names one certificate covers.

    ``server_names`` are the names that certificate holds which no enabled host
    claims on :443 — a disabled host's own name lands here, which is the whole
    point. Sorted, so two nodes render identical text.
    """

    certificate: CertificateSpec
    server_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DesiredState:
    """The complete set of managed objects a render pass should emit.

    Pools are split by nginx context because ``upstream`` blocks are
    context-local: one defined in ``http {}`` is invisible to ``stream {}``.
    Two fields rather than one make it impossible for ``render_config`` to emit
    a stream-only pool into ``http {}``. A pool used by both appears in both
    tuples and is rendered into both directories under the same nginx name —
    separate namespaces, not a collision.

    Each tuple should contain exactly the pools referenced by the objects that
    render alongside it; the loader guarantees this so no orphan ``upstream``
    blocks are written.

    ``proxy_hosts``, ``redirection_hosts`` and ``dead_hosts`` render into the
    HTTP ``conf.d`` directory; ``streams`` render into a separate directory that
    the base config includes from the top-level ``stream {}`` context (TCP/UDP
    forwarding cannot live inside ``http {}``).

    ``default_site`` renders into a third directory the base config includes
    from *inside* its ``default_server`` block; ``None`` means no file is
    written and nginx falls back to its own no-location-match 404.

    ``default_tls`` renders one ``:443`` server block per certificate, serving
    the default site for names that certificate covers but no enabled host
    claims — the HTTPS counterpart to the ``:80`` ``default_server``.
    """

    proxy_hosts: tuple[ProxyHostSpec, ...] = field(default_factory=tuple)
    http_upstreams: tuple[UpstreamSpec, ...] = field(default_factory=tuple)
    stream_upstreams: tuple[UpstreamSpec, ...] = field(default_factory=tuple)
    redirection_hosts: tuple[RedirectionHostSpec, ...] = field(default_factory=tuple)
    dead_hosts: tuple[DeadHostSpec, ...] = field(default_factory=tuple)
    streams: tuple[StreamSpec, ...] = field(default_factory=tuple)
    default_site: DefaultSiteSpec | None = None
    default_tls: tuple[DefaultTlsSpec, ...] = field(default_factory=tuple)


__all__ = [
    "BackendSpec",
    "UpstreamSpec",
    "CertificateSpec",
    "AuthUserSpec",
    "ClientRuleSpec",
    "AccessListSpec",
    "ProxyHostSpec",
    "RedirectionHostSpec",
    "DeadHostSpec",
    "StreamSpec",
    "DefaultSiteSpec",
    "DesiredState",
    "LocationSpec",
]
