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


class WhitelistKind(enum.StrEnum):
    """What a CrowdSec whitelist matches on.

    The two kinds render different YAML and carry different risk.
    ``ip_cidr`` is fully validated before it is written — a bad address is a
    422 and never reaches disk. ``expression`` is CrowdSec's ``expr`` language
    and can only be checked by CrowdSec itself: a expression that does not
    compile is fatal at startup, so a typo is caught by the apply's rollback
    rather than by validation. See ``docs/crowdsec.md``.
    """

    ip_cidr = "ip_cidr"
    expression = "expression"


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


class DefaultSiteMode(enum.StrEnum):
    """What nginx returns for a request matching no configured host."""

    congratulations = "congratulations"
    not_found = "not_found"
    no_response = "no_response"
    redirect = "redirect"
    custom_page = "custom_page"


class CrowdSecBanMode(enum.StrEnum):
    """What a CrowdSec-blocked visitor is served.

    ``none`` is not "unset": it is the deliberate choice to write no template
    file, so the bouncer answers a bare 403 as it did before this setting
    existed. Some operators prefer that a block does not advertise which
    product is in front.
    """

    megoopm = "megoopm"
    custom_page = "custom_page"
    none = "none"


class AuditAction(enum.StrEnum):
    """The mutation an audit-log row records."""

    create = "create"
    update = "update"
    delete = "delete"
    enable = "enable"
    disable = "disable"



class SmtpSecurity(enum.StrEnum):
    """How the SMTP connection is secured."""

    #: Connect in the clear, then upgrade with STARTTLS. Port 587.
    starttls = "starttls"
    #: TLS from the first byte ("SMTPS"). Port 465.
    ssl = "ssl"
    #: No transport security. For a trusted local relay only.
    none = "none"


class AuthTokenKind(enum.StrEnum):
    """What a single-use ``auth_token`` row is for."""

    password_reset = "password_reset"

__all__ = [
    "LoadBalanceMethod",
    "WhitelistKind",
    "CertificateProvider",
    "CertificateStatus",
    "HttpScheme",
    "RedirectScheme",
    "AccessListDirective",
    "DefaultSiteMode",
    "AuditAction",
    "SmtpSecurity",
    "AuthTokenKind",
]
