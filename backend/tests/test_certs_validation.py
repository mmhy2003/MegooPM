"""Validation of uploaded certificate material (no DB, no network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.services.certs.validation import (
    CertificateValidationError,
    inspect_pem,
    validate_certificate,
)

from tests._cert_helpers import generate_key, key_to_pem, make_self_signed


def test_valid_cert_and_key_pass() -> None:
    cert_pem, key_pem = make_self_signed(["example.com", "www.example.com"])
    result = validate_certificate(certificate_pem=cert_pem, private_key_pem=key_pem)

    assert result.domain_names == ["example.com", "www.example.com"]
    assert result.not_valid_after > datetime.now(UTC)
    # Fullchain begins with the leaf certificate.
    assert result.fullchain_pem.startswith("-----BEGIN CERTIFICATE-----")
    assert "BEGIN PRIVATE KEY" in result.privkey_pem


def test_mismatched_key_is_rejected() -> None:
    cert_pem, _ = make_self_signed(["example.com"])
    other_key_pem = key_to_pem(generate_key())

    with pytest.raises(CertificateValidationError, match="does not match"):
        validate_certificate(certificate_pem=cert_pem, private_key_pem=other_key_pem)


def test_expired_cert_is_rejected() -> None:
    now = datetime.now(UTC)
    cert_pem, key_pem = make_self_signed(
        ["old.example.com"],
        not_before=now - timedelta(days=40),
        not_after=now - timedelta(days=10),
    )
    with pytest.raises(CertificateValidationError, match="expired"):
        validate_certificate(certificate_pem=cert_pem, private_key_pem=key_pem)


def test_not_yet_valid_cert_is_rejected() -> None:
    now = datetime.now(UTC)
    cert_pem, key_pem = make_self_signed(
        ["future.example.com"],
        not_before=now + timedelta(days=5),
        not_after=now + timedelta(days=90),
    )
    with pytest.raises(CertificateValidationError, match="not valid until"):
        validate_certificate(certificate_pem=cert_pem, private_key_pem=key_pem)


def test_garbage_pem_is_rejected() -> None:
    _, key_pem = make_self_signed(["example.com"])
    with pytest.raises(CertificateValidationError, match="parse certificate"):
        validate_certificate(certificate_pem="not a cert", private_key_pem=key_pem)


def test_chain_is_appended_to_fullchain() -> None:
    cert_pem, key_pem = make_self_signed(["example.com"])
    intermediate_pem, _ = make_self_signed(["intermediate.example.com"])

    result = validate_certificate(
        certificate_pem=cert_pem, private_key_pem=key_pem, chain_pem=intermediate_pem
    )
    # Leaf + intermediate present, leaf first.
    assert result.fullchain_pem.count("BEGIN CERTIFICATE") == 2
    assert result.chain_pem.count("BEGIN CERTIFICATE") == 1


def test_inspect_pem_returns_domains_and_expiry() -> None:
    cert_pem, _ = make_self_signed(["a.example.com", "b.example.com"])
    domains, expiry = inspect_pem(cert_pem)
    assert domains == ["a.example.com", "b.example.com"]
    assert expiry > datetime.now(UTC)
