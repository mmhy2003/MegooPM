"""Enumerated types shared across the domain model.

These back native Postgres ``ENUM`` types. The SQLAlchemy ``Enum`` columns that
use them pass ``values_callable`` so the *value* (not the member name) is what
lands in the database — keeping the on-disk representation explicit and stable.
"""

from __future__ import annotations

import enum


class LoadBalanceMethod(enum.StrEnum):
    """nginx ``upstream`` load-balancing strategies for a backend pool."""

    round_robin = "round_robin"
    least_conn = "least_conn"
    ip_hash = "ip_hash"
    hash = "hash"
    random = "random"


class UpstreamContext(enum.StrEnum):
    """Which nginx context a pool may be rendered into.

    ``upstream`` blocks are context-local: one defined in ``http {}`` is
    invisible to ``stream {}``, so a pool has to declare where it may be
    attached. This also constrains its load-balancing method — ``ip_hash``
    exists only in ``http``, and using it on a stream pool is a hard
    ``nginx -t`` failure rather than a degraded fallback.
    """

    http = "http"
    stream = "stream"
    both = "both"


class CertificateProvider(enum.StrEnum):
    """How a certificate is obtained/managed."""

    letsencrypt = "letsencrypt"
    custom = "custom"
    self_signed = "self_signed"


class CertificateStatus(enum.StrEnum):
    """Lifecycle state of a managed certificate.

    ``pending`` — a row exists but material has not been issued yet (an ACME
    order is queued/running). ``active`` — valid material is on disk and usable
    by a host. ``failed`` — the last issuance/renewal attempt errored (see
    ``meta['last_error']``). ``expired`` — past ``expires_on`` and not renewed.
    """

    pending = "pending"
    active = "active"
    failed = "failed"
    expired = "expired"


class HttpScheme(enum.StrEnum):
    """Scheme a proxy host uses to reach its upstream."""

    http = "http"
    https = "https"


class RedirectScheme(enum.StrEnum):
    """Scheme applied to a redirection target (``auto`` keeps the request's)."""

    auto = "auto"
    http = "http"
    https = "https"


class AccessListDirective(enum.StrEnum):
    """Client-rule directive for an access list entry."""

    allow = "allow"
    deny = "deny"


class AuditAction(enum.StrEnum):
    """The mutation an audit-log row records."""

    create = "create"
    update = "update"
    delete = "delete"
    enable = "enable"
    disable = "disable"


__all__ = [
    "LoadBalanceMethod",
    "CertificateProvider",
    "CertificateStatus",
    "HttpScheme",
    "RedirectScheme",
    "AccessListDirective",
    "AuditAction",
]
