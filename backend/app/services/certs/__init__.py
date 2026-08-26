"""Certificate management: validation, storage, ACME/self-signed issuance.

Public facade over the pieces so callers (routes, Celery tasks) import from one
place:

* validation — parse/verify uploaded PEM material.
* storage — read/write cert material on the shared certs volume.
* issuers — :class:`AcmeIssuer` (Let's Encrypt) / :class:`SelfSignedIssuer`.
* issuance — orchestrate issue/renew against a certificate row.
* service — CRUD and the custom-upload / Let's Encrypt creation paths.
* renewal — select certificates due for auto-renewal.
"""

from __future__ import annotations

from app.services.certs.acme_client import (
    AcmeIssuer,
    CertIssuer,
    ChallengeType,
    DnsProvider,
    DnsProviderNotConfigured,
    IssuedCertificate,
    ManualDnsProvider,
    SelfSignedIssuer,
)
from app.services.certs.issuance import build_issuer, issue_for_certificate
from app.services.certs.renewal import is_due_for_renewal, list_due_certificate_ids
from app.services.certs.service import (
    CertificateNotFoundError,
    create_custom_certificate,
    create_letsencrypt_certificate,
    delete_certificate,
    get_certificate,
    list_certificates,
)
from app.services.certs.validation import (
    CertificateValidationError,
    ValidatedCertificate,
    inspect_pem,
    validate_certificate,
)

__all__ = [
    "AcmeIssuer",
    "CertIssuer",
    "CertificateNotFoundError",
    "CertificateValidationError",
    "ChallengeType",
    "DnsProvider",
    "DnsProviderNotConfigured",
    "IssuedCertificate",
    "ManualDnsProvider",
    "SelfSignedIssuer",
    "ValidatedCertificate",
    "build_issuer",
    "create_custom_certificate",
    "create_letsencrypt_certificate",
    "delete_certificate",
    "get_certificate",
    "inspect_pem",
    "is_due_for_renewal",
    "issue_for_certificate",
    "list_certificates",
    "list_due_certificate_ids",
    "validate_certificate",
]
