"""The .mgb envelope: streamed AES-256-GCM under a key derived from SECRET_KEY.

Streaming matters: a backup is gigabytes, and a design that holds the plaintext
in memory would fail on the machines this product runs on. Everything here
therefore works on file objects, and every test writes through them.
"""

from __future__ import annotations

import io

import pytest
from app.services.backup.crypto import (
    CHUNK_BYTES,
    BackupFormatError,
    BackupKeyError,
    decrypt_first_chunk,
    decrypt_stream,
    encrypt_stream,
    key_fingerprint,
    read_header,
)

KEY = "a-secret-key"
OTHER = "a-different-secret-key"


def _sealed(payload: bytes, *, secret_key: str = KEY) -> bytes:
    out = io.BytesIO()
    encrypt_stream(io.BytesIO(payload), out, secret_key=secret_key)
    return out.getvalue()


def _opened(sealed: bytes, *, secret_key: str = KEY) -> bytes:
    out = io.BytesIO()
    decrypt_stream(io.BytesIO(sealed), out, secret_key=secret_key)
    return out.getvalue()


def test_a_payload_comes_back_byte_for_byte() -> None:
    payload = b"proxy hosts, certificates, users" * 10
    assert _opened(_sealed(payload)) == payload


def test_a_payload_larger_than_one_chunk_round_trips() -> None:
    # The chunking is the whole design; one-chunk tests would not exercise it.
    payload = bytes(range(256)) * (CHUNK_BYTES // 128)
    assert _opened(_sealed(payload)) == payload


def test_an_empty_payload_round_trips() -> None:
    assert _opened(_sealed(b"")) == b""


def test_another_key_cannot_open_it() -> None:
    with pytest.raises(BackupKeyError):
        _opened(_sealed(b"secrets"), secret_key=OTHER)


def test_a_truncated_file_is_refused_rather_than_half_restored() -> None:
    # Half a backup restored is worse than no backup: the terminator is what
    # tells a reader the file is whole.
    sealed = _sealed(b"x" * (CHUNK_BYTES * 2))
    with pytest.raises(BackupFormatError):
        _opened(sealed[: len(sealed) - 8])


def test_a_flipped_byte_is_refused() -> None:
    sealed = bytearray(_sealed(b"payload"))
    sealed[-10] ^= 0x01
    with pytest.raises(BackupKeyError):
        _opened(bytes(sealed))


def test_a_foreign_file_is_refused_by_its_magic() -> None:
    with pytest.raises(BackupFormatError):
        _opened(b"not a megoopm backup at all" * 4)


def test_the_header_says_which_key_made_it_without_opening_the_file() -> None:
    # The page warns before an admin tries to restore something unreadable.
    header = read_header(io.BytesIO(_sealed(b"payload")))
    assert header.version == 1
    assert header.fingerprint == key_fingerprint(KEY)
    assert header.fingerprint != key_fingerprint(OTHER)


def test_the_first_chunk_can_be_read_alone() -> None:
    # Listing reads meta.json (the tar's first member) without decrypting GBs.
    payload = b"meta first" + b"\0" * (CHUNK_BYTES * 2)
    assert decrypt_first_chunk(io.BytesIO(_sealed(payload)), secret_key=KEY).startswith(
        b"meta first"
    )


def test_each_file_uses_a_fresh_salt_and_nonce() -> None:
    # Reusing a nonce with the same key breaks GCM outright.
    first, second = read_header(io.BytesIO(_sealed(b"x"))), read_header(io.BytesIO(_sealed(b"x")))
    assert first.salt != second.salt
    assert first.nonce_prefix != second.nonce_prefix
