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
