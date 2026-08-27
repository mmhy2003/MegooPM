"""Issuance/renewal orchestration: issuer + storage + certificate row state.

This is the seam the Celery tasks call. It is deliberately DB-session-agnostic —
it mutates a :class:`~app.models.certificate.Certificate` instance (updating
``status``, ``expires_on``, ``domain_names`` and ``meta``) and writes material to
the shared volume, but the caller owns loading and committing the row. That makes
the whole issue → store → record path unit-testable with a plain ``Certificate()``
and an injected issuer, no database required.
"""

from __future__ import annotations

import functools
from datetime import UTC, datetime

from cryptography import x509

from app.core.config import settings
from app.models.certificate import Certificate
from app.models.enums import CertificateProvider, CertificateStatus
from app.services.certs import storage
from app.services.certs.acme_client import (
    AcmeIssuer,
    CertIssuer,
    ChallengeType,
    DnsProvider,
    SelfSignedIssuer,
)
from app.services.certs.dns_providers.propagation import wait_for_txt
from app.services.certs.validation import extract_domain_names


def build_issuer(
    certificate: Certificate,
    *,
    dns_provider: DnsProvider | None = None,
) -> CertIssuer:
    """Return the issuer appropriate for a certificate's provider and settings.

    ``self_signed`` (and any provider when ``acme_self_signed`` is on) yields a
    :class:`SelfSignedIssuer`; ``letsencrypt`` yields an :class:`AcmeIssuer`
    bound to the configured directory URL and challenge type. The challenge type
    and DNS provider come from the certificate's ``meta`` (``challenge`` key);
    DNS-01 issuers also verify propagation on the authoritative nameservers
    before answering.
    """
    if certificate.provider == CertificateProvider.self_signed or settings.acme_self_signed:
        return SelfSignedIssuer()

    meta = certificate.meta or {}
    challenge_type = meta.get("challenge", ChallengeType.HTTP_01)
    propagation_check = None
    if challenge_type == ChallengeType.DNS_01:
        propagation_check = functools.partial(
            wait_for_txt,
            timeout_seconds=settings.acme_dns_propagation_timeout_seconds,
            interval_seconds=settings.acme_dns_propagation_interval_seconds,
        )
    return AcmeIssuer(
        directory_url=meta.get("directory_url") or settings.acme_directory_url,
        account_email=meta.get("account_email") or settings.acme_account_email,
        http_challenge_dir=settings.acme_http_challenge_dir,
        read_account_key=lambda: storage.read_account_key(
            settings.nginx_certs_dir,
            settings.acme_directory_url,
            settings.acme_account_email,
        ),
        write_account_key=lambda pem: storage.write_account_key(
            settings.nginx_certs_dir,
            settings.acme_directory_url,
            settings.acme_account_email,
            pem,
        ),
        challenge_type=challenge_type,
        dns_provider=dns_provider,
        propagation_check=propagation_check,
    )


def issue_for_certificate(
    certificate: Certificate,
    *,
    issuer: CertIssuer,
    certs_dir: str | None = None,
    now: datetime | None = None,
) -> None:
    """Issue/renew ``certificate`` with ``issuer`` and persist material to disk.

    On success the certificate's ``status`` becomes ``active``, ``expires_on`` is
    set from the issued material, and (for ACME) ``domain_names`` is refreshed
    from the leaf's SANs. On failure ``status`` becomes ``failed`` and the error
    is recorded in ``meta['last_error']``; the exception is re-raised so the
    Celery task is marked failed too.
    """
    certs_dir = certs_dir or settings.nginx_certs_dir
    now = now or datetime.now(UTC)

    if certificate.id is None:
        raise ValueError("Certificate must be persisted (have an id) before issuance")
    if not certificate.domain_names:
        raise ValueError("Certificate has no domain names to issue for")

    try:
        issued = issuer.issue(list(certificate.domain_names))
        storage.write_material(
            certs_dir,
            certificate.id,
            fullchain_pem=issued.fullchain_pem,
            privkey_pem=issued.privkey_pem,
        )
        leaf = x509.load_pem_x509_certificates(issued.fullchain_pem.encode())[0]
        certificate.domain_names = extract_domain_names(leaf) or list(certificate.domain_names)
        certificate.expires_on = issued.not_valid_after
        certificate.status = CertificateStatus.active
        certificate.meta = {
            **(certificate.meta or {}),
            "last_error": None,
            "issued_at": now.isoformat(),
        }
    except Exception as exc:
        certificate.status = CertificateStatus.failed
        certificate.meta = {
            **(certificate.meta or {}),
            "last_error": str(exc),
            "failed_at": now.isoformat(),
        }
        raise


__all__ = ["build_issuer", "issue_for_certificate"]
