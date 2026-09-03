"""Unit tests for password hashing and JWT primitives."""

from __future__ import annotations

import jwt
import pytest
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_hash_is_salted_and_verifiable() -> None:
    hashed = hash_password("s3cret-pass")
    assert hashed != "s3cret-pass"
    assert hashed.startswith("$argon2")
    assert verify_password("s3cret-pass", hashed)
    assert not verify_password("wrong", hashed)


def test_hash_is_unique_per_call() -> None:
    assert hash_password("same") != hash_password("same")


def test_verify_tolerates_malformed_hash() -> None:
    assert not verify_password("x", "not-a-real-hash")


def test_access_token_roundtrip_carries_role() -> None:
    token = create_access_token(42, "admin", token_version=0)
    payload = decode_token(token, expected_type="access")
    assert payload["sub"] == "42"
    assert payload["role"] == "admin"
    assert payload["type"] == "access"


def test_refresh_token_roundtrip() -> None:
    token = create_refresh_token(7, token_version=0)
    payload = decode_token(token, expected_type="refresh")
    assert payload["sub"] == "7"
    assert payload["type"] == "refresh"


def test_decode_rejects_wrong_token_type() -> None:
    access = create_access_token(1, "member", token_version=0)
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(access, expected_type="refresh")


def test_decode_rejects_tampered_signature() -> None:
    token = create_access_token(1, "member", token_version=0)
    tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
    with pytest.raises(jwt.PyJWTError):
        decode_token(tampered, expected_type="access")
