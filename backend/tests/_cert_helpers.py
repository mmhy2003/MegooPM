"""Test helpers for generating certificate material with ``cryptography``.

Not a test module (no ``test_`` prefix) — imported by the certificate tests to
build self-signed certs/keys without any network or ACME server.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

_PEM = serialization.Encoding.PEM


def generate_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def key_to_pem(key: rsa.RSAPrivateKey) -> str:
    return key.private_bytes(
        _PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def make_self_signed(
    domains: list[str],
    *,
    not_before: datetime | None = None,
    not_after: datetime | None = None,
    key: rsa.RSAPrivateKey | None = None,
) -> tuple[str, str]:
    """Return ``(certificate_pem, private_key_pem)`` for ``domains``."""
    now = datetime.now(UTC)
    not_before = not_before or (now - timedelta(days=1))
    not_after = not_after or (now + timedelta(days=90))
    key = key or generate_key()

    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domains[0])])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(d) for d in domains]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(_PEM).decode(), key_to_pem(key)
