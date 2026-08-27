"""Issuance orchestration against a certificate row (no DB, no network).

Uses the real :class:`SelfSignedIssuer` so the full issue -> store -> record
path is exercised with genuine certificate parsing, plus a failing issuer to
prove the error path records ``status=failed`` and re-raises.
"""

from __future__ import annotations

import pytest
from app.models.certificate import Certificate
from app.models.enums import CertificateProvider, CertificateStatus
from app.services.certs import storage
from app.services.certs.acme_client import IssuedCertificate, SelfSignedIssuer
from app.services.certs.issuance import issue_for_certificate


def _pending_cert(cert_id: int = 5) -> Certificate:
    cert = Certificate(
        name="test",
        provider=CertificateProvider.letsencrypt,
        status=CertificateStatus.pending,
        domain_names=["example.com"],
        meta={},
    )
    cert.id = cert_id
    return cert


class _FailingIssuer:
    def issue(self, domain_names: list[str]) -> IssuedCertificate:
        raise RuntimeError("ACME order failed: DNS lookup error")


def test_successful_issuance_updates_row_and_writes_material(tmp_path) -> None:
    cert = _pending_cert()
    issue_for_certificate(cert, issuer=SelfSignedIssuer(), certs_dir=str(tmp_path))

    assert cert.status == CertificateStatus.active
    assert cert.expires_on is not None
    assert cert.domain_names == ["example.com"]
    assert cert.meta["last_error"] is None
    assert storage.material_exists(str(tmp_path), cert.id)


def test_domains_refreshed_from_issued_leaf(tmp_path) -> None:
    cert = _pending_cert()
    cert.domain_names = ["multi.example.com", "alt.example.com"]
    issue_for_certificate(cert, issuer=SelfSignedIssuer(), certs_dir=str(tmp_path))
    assert cert.domain_names == ["multi.example.com", "alt.example.com"]


def test_failed_issuance_records_error_and_reraises(tmp_path) -> None:
    cert = _pending_cert()
    with pytest.raises(RuntimeError, match="ACME order failed"):
        issue_for_certificate(cert, issuer=_FailingIssuer(), certs_dir=str(tmp_path))

    assert cert.status == CertificateStatus.failed
    assert "ACME order failed" in cert.meta["last_error"]
    assert not storage.material_exists(str(tmp_path), cert.id)


def test_issuance_requires_persisted_id(tmp_path) -> None:
    cert = _pending_cert()
    cert.id = None
    with pytest.raises(ValueError, match="have an id"):
        issue_for_certificate(cert, issuer=SelfSignedIssuer(), certs_dir=str(tmp_path))


# --- DNS-01 propagation hook ----------------------------------------------------

from app.services.certs.acme_client import AcmeIssuer, ChallengeType  # noqa: E402
from app.services.certs.issuance import build_issuer  # noqa: E402


class _RecordingProvider:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def set_txt_record(self, name: str, value: str) -> None:
        self.log.append(f"set:{name}={value}")

    def remove_txt_record(self, name: str, value: str) -> None:
        self.log.append(f"remove:{name}={value}")


class _Chall:
    @staticmethod
    def validation_domain_name(domain: str) -> str:
        return f"_acme-challenge.{domain}"


class _Challb:
    chall = _Chall()


def _dns_issuer(tmp_path, provider, check) -> AcmeIssuer:
    return AcmeIssuer(
        directory_url="https://acme.invalid/directory",
        account_email=None,
        http_challenge_dir=str(tmp_path),
        read_account_key=lambda: None,
        write_account_key=lambda pem: None,
        challenge_type=ChallengeType.DNS_01,
        dns_provider=provider,
        propagation_check=check,
    )


def test_dns01_provisioning_sets_record_then_waits_for_propagation(tmp_path) -> None:
    log: list[str] = []
    issuer = _dns_issuer(
        tmp_path, _RecordingProvider(log), lambda n, v: log.append(f"check:{n}={v}")
    )
    cleanups: list = []

    issuer._provision_challenge(_Challb(), "example.com", "tok", cleanups)

    assert log == ["set:_acme-challenge.example.com=tok", "check:_acme-challenge.example.com=tok"]
    assert cleanups == [("_acme-challenge.example.com", "tok")]
    issuer._cleanup_challenges(cleanups)
    assert log[-1] == "remove:_acme-challenge.example.com=tok"


def test_dns01_propagation_failure_propagates_but_keeps_cleanup_entry(tmp_path) -> None:
    log: list[str] = []

    def failing_check(name: str, value: str) -> None:
        raise RuntimeError("not propagated")

    issuer = _dns_issuer(tmp_path, _RecordingProvider(log), failing_check)
    cleanups: list = []
    with pytest.raises(RuntimeError, match="not propagated"):
        issuer._provision_challenge(_Challb(), "example.com", "tok", cleanups)
    # issue() runs _cleanup_challenges in a finally block, so the entry must exist.
    assert cleanups == [("_acme-challenge.example.com", "tok")]


def test_build_issuer_wires_propagation_check_only_for_dns01() -> None:
    dns_cert = _pending_cert()
    dns_cert.meta = {"challenge": "dns-01"}
    http_cert = _pending_cert()
    http_cert.meta = {"challenge": "http-01"}

    assert build_issuer(dns_cert)._propagation_check is not None
    assert build_issuer(http_cert)._propagation_check is None
