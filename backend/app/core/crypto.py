"""Reversible encryption of secrets stored at rest (MEG-43).

Some secrets must be recoverable in plaintext to be *used* — unlike passwords
(one-way Argon2id/apr1 hashes), a CrowdSec machine password or bouncer API key
has to be replayed verbatim to the upstream LAPI. Those secrets are therefore
encrypted (not hashed) before they touch the database.

The scheme is Fernet (AES-128-CBC + HMAC-SHA256, authenticated) with the key
*derived* from the application ``secret_key`` — the same root secret that signs
JWTs — so there is no new key to distribute or rotate independently. Deriving
via SHA-256 lets an operator use any-length ``secret_key`` while Fernet still
gets the 32 url-safe-base64 bytes it requires.

Ciphertext is stored as the Fernet token string (already url-safe base64), so
the DB columns are plain text and no binary handling is needed. Rotating
``secret_key`` invalidates existing ciphertext by design — re-registration (or a
re-seed from env) repopulates it.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings
from app.core.config import settings as default_settings


class SecretDecryptError(RuntimeError):
    """Stored ciphertext could not be decrypted (wrong key or corruption)."""


def _fernet(settings: Settings) -> Fernet:
    """Build a Fernet from the app ``secret_key`` (SHA-256 → 32 url-safe bytes)."""
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str, *, settings: Settings | None = None) -> str:
    """Encrypt ``plaintext`` into a Fernet token (url-safe base64 string)."""
    return _fernet(settings or default_settings).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str, *, settings: Settings | None = None) -> str:
    """Decrypt a Fernet ``token`` produced by :func:`encrypt_secret`.

    Raises :class:`SecretDecryptError` if the token is invalid — typically a
    ``secret_key`` change since the value was written.
    """
    try:
        return _fernet(settings or default_settings).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise SecretDecryptError("Could not decrypt stored secret (secret_key changed?).") from exc


__all__ = ["SecretDecryptError", "decrypt_secret", "encrypt_secret"]
