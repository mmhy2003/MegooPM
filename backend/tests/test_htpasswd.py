"""Unit tests for the apr1 (salted MD5) htpasswd hashing used by access lists.

The golden vectors below were produced with ``openssl passwd -apr1 -salt <salt>
<password>`` — an independent implementation — so a match proves byte-for-byte
compatibility with what nginx's ``auth_basic_user_file`` expects.
"""

from __future__ import annotations

import pytest
from app.services.htpasswd import hash_apr1, htpasswd_line, verify_apr1

# (password, salt, expected) — from `openssl passwd -apr1 -salt SALT PASSWORD`.
_VECTORS = [
    ("password", "abcd1234", "$apr1$abcd1234$kDEexREaC0S6a7lHugd.L."),
    ("s3cr3t!", "Xy9pQ2rT", "$apr1$Xy9pQ2rT$cJhfbcZrtqu5x.FjzKd5S."),
    ("hi", "aB", "$apr1$aB$qvQAvxjcslHRQgzg5f5lP1"),
    ("correct horse battery staple", "12345678", "$apr1$12345678$5s8zqQNNXgdW9osfkSNGf0"),
]


@pytest.mark.parametrize("password,salt,expected", _VECTORS)
def test_matches_openssl_golden_vectors(password: str, salt: str, expected: str) -> None:
    assert hash_apr1(password, salt=salt) == expected


@pytest.mark.parametrize("password,salt,expected", _VECTORS)
def test_verify_accepts_correct_password(password: str, salt: str, expected: str) -> None:
    assert verify_apr1(password, expected) is True
    assert verify_apr1(password + "x", expected) is False


def test_random_salt_roundtrips_and_is_unique() -> None:
    a = hash_apr1("swordfish")
    b = hash_apr1("swordfish")
    assert a != b  # random salt → distinct hashes
    assert a.startswith("$apr1$")
    assert verify_apr1("swordfish", a)
    assert verify_apr1("swordfish", b)


def test_verify_rejects_malformed_hash() -> None:
    assert verify_apr1("x", "not-a-hash") is False
    assert verify_apr1("x", "$6$notapr1$whatever") is False
    assert verify_apr1("x", "") is False


def test_salt_longer_than_eight_is_rejected() -> None:
    with pytest.raises(ValueError):
        hash_apr1("x", salt="123456789")


def test_htpasswd_line_format() -> None:
    assert htpasswd_line("alice", "$apr1$abc$def") == "alice:$apr1$abc$def"
