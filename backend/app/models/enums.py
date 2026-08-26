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


class CertificateProvider(enum.StrEnum):
    """How a certificate is obtained/managed."""

    letsencrypt = "letsencrypt"
    custom = "custom"
    self_signed = "self_signed"


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
    "HttpScheme",
    "RedirectScheme",
    "AccessListDirective",
    "AuditAction",
]
