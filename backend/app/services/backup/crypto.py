"""The .mgb envelope: streamed AES-256-GCM under a key derived from SECRET_KEY.

A backup is gigabytes, so nothing here holds a payload in memory: callers pass
file objects and the data moves in 1 MiB chunks, each sealed on its own. The
clear header carries what a reader needs *before* it can decrypt anything —
the format version, the salt, and a fingerprint of the key that made the file,
so the UI can say "this was made with a different SECRET_KEY" instead of
failing in the middle of a restore.

The terminator is load bearing. Without it a file cut short by a full disk
would decrypt cleanly up to the cut and look like a complete, shorter backup.
"""

from __future__ import annotations

import hashlib
import os
import struct
from dataclasses import dataclass
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"MEGOOPMBK"
FORMAT_VERSION = 1
SALT_BYTES = 16
FINGERPRINT_BYTES = 8
NONCE_PREFIX_BYTES = 8
HEADER_BYTES = len(MAGIC) + 1 + SALT_BYTES + FINGERPRINT_BYTES + NONCE_PREFIX_BYTES  # 42
CHUNK_BYTES = 1024 * 1024
_INFO = b"megoopm-backup-v1"
_TERMINATOR = b"\x00\x00\x00\x00"


class BackupFormatError(RuntimeError):
    """The file is not a MegooPM backup, or is incomplete."""


class BackupKeyError(RuntimeError):
    """The file cannot be opened with this instance's SECRET_KEY."""


@dataclass(frozen=True, slots=True)
class Header:
    version: int
    salt: bytes
    fingerprint: str
    nonce_prefix: bytes
    #: The header bytes verbatim; used as AEAD associated data.
    raw: bytes


def key_fingerprint(secret_key: str) -> str:
    """Which key made (or can open) a file, without revealing the key."""
    digest = hashlib.sha256(_INFO + secret_key.encode("utf-8")).digest()
    return digest[:FINGERPRINT_BYTES].hex()


def _derive(secret_key: str, salt: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=_INFO).derive(
        secret_key.encode("utf-8")
    )


def read_header(src: BinaryIO) -> Header:
    raw = src.read(HEADER_BYTES)
    if len(raw) < HEADER_BYTES or not raw.startswith(MAGIC):
        raise BackupFormatError("not a MegooPM backup file")
    version = raw[len(MAGIC)]
    if version != FORMAT_VERSION:
        raise BackupFormatError(f"unsupported backup format version {version}")
    offset = len(MAGIC) + 1
    salt = raw[offset : offset + SALT_BYTES]
    offset += SALT_BYTES
    fingerprint = raw[offset : offset + FINGERPRINT_BYTES].hex()
    offset += FINGERPRINT_BYTES
    nonce_prefix = raw[offset : offset + NONCE_PREFIX_BYTES]
    return Header(
        version=version, salt=salt, fingerprint=fingerprint, nonce_prefix=nonce_prefix, raw=raw
    )


def encrypt_stream(src: BinaryIO, dst: BinaryIO, *, secret_key: str) -> None:
    salt = os.urandom(SALT_BYTES)
    nonce_prefix = os.urandom(NONCE_PREFIX_BYTES)
    header = (
        MAGIC
        + bytes([FORMAT_VERSION])
        + salt
        + bytes.fromhex(key_fingerprint(secret_key))
        + nonce_prefix
    )
    dst.write(header)

    aead = AESGCM(_derive(secret_key, salt))
    counter = 0
    while True:
        plain = src.read(CHUNK_BYTES)
        if not plain:
            break
        sealed = aead.encrypt(nonce_prefix + struct.pack(">I", counter), plain, header)
        dst.write(struct.pack(">I", len(sealed)))
        dst.write(sealed)
        counter += 1
    dst.write(_TERMINATOR)


def _chunks(src: BinaryIO, header: Header, aead: AESGCM):
    counter = 0
    while True:
        length_bytes = src.read(4)
        if len(length_bytes) < 4:
            raise BackupFormatError("backup file ends mid-chunk")
        (length,) = struct.unpack(">I", length_bytes)
        if length == 0:
            return
        sealed = src.read(length)
        if len(sealed) < length:
            raise BackupFormatError("backup file ends mid-chunk")
        try:
            yield aead.decrypt(header.nonce_prefix + struct.pack(">I", counter), sealed, header.raw)
        except InvalidTag as exc:
            raise BackupKeyError("wrong SECRET_KEY, or the backup is damaged") from exc
        counter += 1


def decrypt_stream(src: BinaryIO, dst: BinaryIO, *, secret_key: str) -> Header:
    header = read_header(src)
    aead = AESGCM(_derive(secret_key, header.salt))
    for plain in _chunks(src, header, aead):
        dst.write(plain)
    return header


def decrypt_first_chunk(src: BinaryIO, *, secret_key: str) -> bytes:
    """Open only the first chunk — enough for the archive's meta.json member."""
    header = read_header(src)
    aead = AESGCM(_derive(secret_key, header.salt))
    for plain in _chunks(src, header, aead):
        return plain
    return b""
