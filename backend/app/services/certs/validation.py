"""Validation and inspection of PEM certificate material (``cryptography``).

Used by the custom-upload path to reject bad input *before* anything is written
to disk or the database, and by the issuance path to read expiry/domains out of
freshly issued certificates.

A valid upload must satisfy:

* the certificate parses as PEM and is currently within its validity window
  (a not-yet-valid or already-expired cert is rejected);
* the supplied private key parses and mathematically matches the certificate's
  public key (so nginx will actually serve the pair);
* any supplied chain parses as one or more PEM certificates.

The public entry point returns a :class:`ValidatedCertificate` carrying the
normalized ``fullchain.pem`` (leaf + chain), the private key, and the metadata
(domain names, validity dates) extracted from the leaf.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_private_key,
)
from cryptography.x509.oid import ExtensionOID, NameOID


class CertificateValidationError(ValueError):
    """Raised when uploaded certificate material is invalid or inconsistent."""


@dataclass(frozen=True, slots=True)
class ValidatedCertificate:
    """The result of validating a certificate + key (+ optional chain)."""

    fullchain_pem: str
    privkey_pem: str
    chain_pem: str
    domain_names: list[str]
    not_valid_before: datetime
    not_valid_after: datetime


def _load_cert(pem: str) -> x509.Certificate:
    try:
        return x509.load_pem_x509_certificate(pem.encode())
    except ValueError as exc:
        raise CertificateValidationError(f"Could not parse certificate: {exc}") from exc


def _load_chain(pem: str) -> list[x509.Certificate]:
    """Parse zero or more concatenated PEM certificates from ``pem``."""
    text = pem.strip()
    if not text:
        return []
    try:
        return x509.load_pem_x509_certificates(text.encode())
    except ValueError as exc:
        raise CertificateValidationError(f"Could not parse certificate chain: {exc}") from exc


def _spki(public_key) -> bytes:  # noqa: ANN001 - cryptography key union type
    """SubjectPublicKeyInfo DER bytes — a canonical public-key fingerprint."""
    return public_key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)


def _not_valid_after(cert: x509.Certificate) -> datetime:
    # ``not_valid_after_utc`` is timezone-aware; the deprecated naive property is
    # avoided so downstream comparisons are unambiguous.
    return cert.not_valid_after_utc


def _not_valid_before(cert: x509.Certificate) -> datetime:
    return cert.not_valid_before_utc


def extract_domain_names(cert: x509.Certificate) -> list[str]:
    """Return the DNS names a certificate covers (SAN, plus CN as fallback).

    Order is preserved and duplicates removed; the SAN is authoritative, and the
    Common Name is only appended when it is not already present.
    """
    names: list[str] = []
    try:
        san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
        names.extend(san.get_values_for_type(x509.DNSName))
    except x509.ExtensionNotFound:
        pass

    for attr in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME):
        cn = attr.value
        if isinstance(cn, str) and cn not in names:
            names.append(cn)

    # De-dup while preserving order.
    seen: set[str] = set()
    return [n for n in names if not (n in seen or seen.add(n))]


def _normalize_privkey(pem: str) -> str:
    """Parse the private key and re-serialize to canonical PKCS#8 PEM."""
    try:
        key = load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError) as exc:
        raise CertificateValidationError(f"Could not parse private key: {exc}") from exc
    return key.private_bytes(
        Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def validate_certificate(
    *,
    certificate_pem: str,
    private_key_pem: str,
    chain_pem: str | None = None,
    now: datetime | None = None,
) -> ValidatedCertificate:
    """Validate a certificate/key/chain triple and return the normalized bundle.

    Raises :class:`CertificateValidationError` with a human-readable message on
    any inconsistency. ``now`` overrides the current time for validity checks
    (used by tests).
    """
    now = now or datetime.now(UTC)

    leaf = _load_cert(certificate_pem)
    chain = _load_chain(chain_pem or "")

    # Key must match the certificate, else nginx serves a broken pair.
    try:
        key = load_pem_private_key(private_key_pem.encode(), password=None)
    except (ValueError, TypeError) as exc:
        raise CertificateValidationError(f"Could not parse private key: {exc}") from exc
    if _spki(key.public_key()) != _spki(leaf.public_key()):
        raise CertificateValidationError("Private key does not match the certificate's public key")

    not_before = _not_valid_before(leaf)
    not_after = _not_valid_after(leaf)
    if now < not_before:
        raise CertificateValidationError(f"Certificate is not valid until {not_before.isoformat()}")
    if now > not_after:
        raise CertificateValidationError(f"Certificate expired on {not_after.isoformat()}")

    domains = extract_domain_names(leaf)
    if not domains:
        raise CertificateValidationError(
            "Certificate has no DNS names (no SAN entries or Common Name)"
        )

    leaf_pem = leaf.public_bytes(Encoding.PEM).decode()
    chain_only_pem = "".join(c.public_bytes(Encoding.PEM).decode() for c in chain)
    fullchain_pem = leaf_pem + chain_only_pem
    normalized_key = _normalize_privkey(private_key_pem)

    return ValidatedCertificate(
        fullchain_pem=fullchain_pem,
        privkey_pem=normalized_key,
        chain_pem=chain_only_pem,
        domain_names=domains,
        not_valid_before=not_before,
        not_valid_after=not_after,
    )


def inspect_pem(fullchain_pem: str) -> tuple[list[str], datetime]:
    """Return ``(domain_names, not_valid_after)`` for the leaf of a fullchain."""
    certs = _load_chain(fullchain_pem)
    if not certs:
        raise CertificateValidationError("No certificate found in PEM data")
    leaf = certs[0]
    return extract_domain_names(leaf), _not_valid_after(leaf)


__all__ = [
    "CertificateValidationError",
    "ValidatedCertificate",
    "extract_domain_names",
    "inspect_pem",
    "validate_certificate",
]
