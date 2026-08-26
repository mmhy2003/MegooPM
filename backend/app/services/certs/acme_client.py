"""Certificate issuers — the pluggable seam that actually mints certificates.

Two implementations back the same :class:`CertIssuer` protocol:

* :class:`AcmeIssuer` — real ACME (Let's Encrypt) issuance via the ``acme``
  library. Solves **HTTP-01** by dropping the token file in a webroot nginx
  serves, and **DNS-01** by delegating record writes to a :class:`DnsProvider`.
* :class:`SelfSignedIssuer` — mints a self-signed certificate locally with no
  network or public DNS. Backs the ``self_signed`` provider and is what the test
  suite (and ``acme_self_signed`` dev mode) injects to exercise the full
  issuance → storage → reload path deterministically.

Keeping issuance behind a protocol is what makes the orchestration in
``issuance.py`` and the Celery tasks unit-testable without a live ACME server.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


@dataclass(frozen=True, slots=True)
class IssuedCertificate:
    """Freshly issued material returned by an issuer."""

    fullchain_pem: str
    privkey_pem: str
    not_valid_after: datetime


class ChallengeType:
    """ACME challenge selectors (values match the ``acme`` type strings)."""

    HTTP_01 = "http-01"
    DNS_01 = "dns-01"


class CertIssuer(Protocol):
    """Anything that can turn a set of domains into certificate material."""

    def issue(self, domain_names: list[str]) -> IssuedCertificate: ...


class DnsProvider(Protocol):
    """Sets/removes the ``_acme-challenge`` TXT records DNS-01 needs."""

    def set_txt_record(self, name: str, value: str) -> None: ...

    def remove_txt_record(self, name: str, value: str) -> None: ...


class DnsProviderNotConfigured(RuntimeError):
    """Raised when a DNS-01 challenge is required but no provider is wired."""


class ManualDnsProvider:
    """Placeholder DNS provider that refuses to act.

    Real deployments inject a provider that talks to their DNS API. Until then,
    requesting a DNS-01 issuance fails loudly rather than hanging on a TXT record
    that will never be published.
    """

    def set_txt_record(self, name: str, value: str) -> None:  # noqa: D102
        raise DnsProviderNotConfigured(
            f"DNS-01 requires publishing TXT {name}={value!r}, but no DNS "
            "provider is configured. Configure a DNS provider or use HTTP-01."
        )

    def remove_txt_record(self, name: str, value: str) -> None:  # noqa: D102
        # Nothing was set, so cleanup is a no-op.
        return None


def _generate_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _key_to_pem(key: rsa.RSAPrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _build_csr(key: rsa.RSAPrivateKey, domain_names: list[str]) -> x509.CertificateSigningRequest:
    builder = x509.CertificateSigningRequestBuilder().subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain_names[0])])
    )
    builder = builder.add_extension(
        x509.SubjectAlternativeName([x509.DNSName(d) for d in domain_names]),
        critical=False,
    )
    return builder.sign(key, hashes.SHA256())


class SelfSignedIssuer:
    """Mint a self-signed certificate covering the requested domains."""

    def __init__(self, *, valid_days: int = 90) -> None:
        self._valid_days = valid_days

    def issue(self, domain_names: list[str]) -> IssuedCertificate:
        if not domain_names:
            raise ValueError("At least one domain name is required")
        key = _generate_key()
        now = datetime.now(UTC)
        not_after = now + timedelta(days=self._valid_days)
        subject = issuer = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, domain_names[0])]
        )
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(not_after)
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(d) for d in domain_names]),
                critical=False,
            )
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )
        return IssuedCertificate(
            fullchain_pem=cert.public_bytes(serialization.Encoding.PEM).decode(),
            privkey_pem=_key_to_pem(key),
            not_valid_after=not_after,
        )


class AcmeIssuer:
    """Issue certificates over ACME (Let's Encrypt) using the ``acme`` library.

    ``challenge_type`` selects HTTP-01 (default) or DNS-01. HTTP-01 writes the
    validation token under ``http_challenge_dir`` (served by nginx at
    ``/.well-known/acme-challenge/``); DNS-01 publishes a TXT record via
    ``dns_provider``.

    The ACME account key is loaded from / persisted to the shared certs volume
    via the injected ``account_key_store`` callbacks, so the account is created
    once and reused across issuances.
    """

    def __init__(
        self,
        *,
        directory_url: str,
        account_email: str | None,
        http_challenge_dir: str,
        read_account_key,  # () -> str | None
        write_account_key,  # (pem: str) -> None
        challenge_type: str = ChallengeType.HTTP_01,
        dns_provider: DnsProvider | None = None,
    ) -> None:
        self._directory_url = directory_url
        self._account_email = account_email
        self._http_challenge_dir = http_challenge_dir
        self._read_account_key = read_account_key
        self._write_account_key = write_account_key
        self._challenge_type = challenge_type
        self._dns_provider = dns_provider

    def _load_or_create_account_key(self):
        """Return a ``josepy`` JWK for the ACME account, creating one if needed."""
        import josepy as jose

        existing = self._read_account_key()
        if existing:
            key = serialization.load_pem_private_key(existing.encode(), password=None)
            return jose.JWKRSA(key=key)
        key = _generate_key()
        self._write_account_key(_key_to_pem(key))
        return jose.JWKRSA(key=key)

    def _new_client(self, account_jwk):
        from acme import client, messages

        net = client.ClientNetwork(account_jwk, user_agent="megoopm-acme")
        directory = client.ClientV2.get_directory(self._directory_url, net)
        acme_client = client.ClientV2(directory, net=net)
        registration = messages.NewRegistration.from_data(
            email=self._account_email, terms_of_service_agreed=True
        )
        try:
            acme_client.new_account(registration)
        except Exception:
            # Already-registered account keys raise; that is fine — reuse it.
            pass
        return acme_client

    def _select_challenge(self, authz, account_jwk):
        from acme import challenges

        want = (
            challenges.HTTP01
            if self._challenge_type == ChallengeType.HTTP_01
            else challenges.DNS01
        )
        for challb in authz.body.challenges:
            if isinstance(challb.chall, want):
                return challb
        raise RuntimeError(f"ACME server offered no {self._challenge_type} challenge")

    def issue(self, domain_names: list[str]) -> IssuedCertificate:
        if not domain_names:
            raise ValueError("At least one domain name is required")

        account_jwk = self._load_or_create_account_key()
        acme_client = self._new_client(account_jwk)

        cert_key = _generate_key()
        csr = _build_csr(cert_key, domain_names)
        csr_pem = csr.public_bytes(serialization.Encoding.PEM)
        order = acme_client.new_order(csr_pem)

        cleanups: list[tuple[str, str]] = []
        try:
            for authz in order.authorizations:
                domain = authz.body.identifier.value
                challb = self._select_challenge(authz, account_jwk)
                response, validation = challb.response_and_validation(account_jwk)
                self._provision_challenge(challb, domain, validation, cleanups)
                acme_client.answer_challenge(challb, response)
            finalized = acme_client.poll_and_finalize(order)
        finally:
            self._cleanup_challenges(cleanups)

        fullchain_pem = finalized.fullchain_pem
        not_after = _leaf_expiry(fullchain_pem)
        return IssuedCertificate(
            fullchain_pem=fullchain_pem,
            privkey_pem=_key_to_pem(cert_key),
            not_valid_after=not_after,
        )

    def _provision_challenge(
        self, challb, domain: str, validation: str, cleanups: list
    ) -> None:
        if self._challenge_type == ChallengeType.HTTP_01:
            token = challb.chall.encode("token")
            path = Path(self._http_challenge_dir)
            path.mkdir(parents=True, exist_ok=True)
            (path / token).write_text(validation)
        else:
            provider = self._dns_provider or ManualDnsProvider()
            name = challb.chall.validation_domain_name(domain)
            provider.set_txt_record(name, validation)
            cleanups.append((name, validation))

    def _cleanup_challenges(self, cleanups: list) -> None:
        if self._challenge_type == ChallengeType.DNS_01 and self._dns_provider:
            for name, value in cleanups:
                try:
                    self._dns_provider.remove_txt_record(name, value)
                except Exception:  # noqa: BLE001 - cleanup must not mask issuance result
                    pass


def _leaf_expiry(fullchain_pem: str) -> datetime:
    certs = x509.load_pem_x509_certificates(fullchain_pem.encode())
    return certs[0].not_valid_after_utc


__all__ = [
    "AcmeIssuer",
    "CertIssuer",
    "ChallengeType",
    "DnsProvider",
    "DnsProviderNotConfigured",
    "IssuedCertificate",
    "ManualDnsProvider",
    "SelfSignedIssuer",
]
