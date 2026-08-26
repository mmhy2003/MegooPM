"""Apache ``apr1`` (salted MD5) password hashing for nginx basic auth.

Access-list basic-auth credentials are enforced by nginx via ``auth_basic`` and
an ``auth_basic_user_file`` (an htpasswd file). nginx cannot verify MegooPM's
primary Argon2id hashes (see :mod:`app.core.security`), so access-list passwords
are hashed in the ``$apr1$`` format instead — the salted-MD5 scheme ``htpasswd
-m`` produces by default and that every nginx build understands natively.

The algorithm is implemented in pure Python (no external dependency, and the
stdlib ``crypt`` module is deprecated/removed) so it is fully unit-testable and
portable. Hashing happens once, at credential-write time; the nginx renderer
only ever emits the stored ``username:hash`` line, so it stays a pure function.
"""

from __future__ import annotations

import hashlib
import secrets

# apr1 uses a bespoke base64 alphabet with a non-standard ordering.
_ITOA64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_SALT_CHARS = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_MAGIC = b"$apr1$"


def _to64(value: int, count: int) -> str:
    """Encode ``value`` into ``count`` apr1-base64 characters (little-endian)."""
    out = []
    for _ in range(count):
        out.append(_ITOA64[value & 0x3F])
        value >>= 6
    return "".join(out)


def _crypt_apr1(password: str, salt: str) -> str:
    """Compute the ``$apr1$`` hash of ``password`` with the given ``salt``.

    This is the Apache MD5 variant (a.k.a. ``htpasswd -m``); ``salt`` must be at
    most 8 characters from the apr1 alphabet.
    """
    pw = password.encode("utf-8")
    salt_b = salt.encode("ascii")

    # Primary digest: password + magic + salt.
    ctx = hashlib.md5(pw + _MAGIC + salt_b)

    # "alternate" digest of password + salt + password, folded in one MD5-block
    # (16 bytes) at a time for as many bytes as the password is long.
    alt = hashlib.md5(pw + salt_b + pw).digest()
    length = len(pw)
    while length > 0:
        ctx.update(alt[: min(16, length)])
        length -= 16

    # Then, for every set bit of the password length, add a NUL byte; for every
    # cleared bit, add the password's first byte.
    length = len(pw)
    while length > 0:
        if length & 1:
            ctx.update(b"\x00")
        else:
            ctx.update(pw[:1])
        length >>= 1

    digest = ctx.digest()

    # 1000 iterations of deliberate key-stretching.
    for i in range(1000):
        ctx = hashlib.md5()
        ctx.update(pw if i & 1 else digest)
        if i % 3:
            ctx.update(salt_b)
        if i % 7:
            ctx.update(pw)
        ctx.update(digest if i & 1 else pw)
        digest = ctx.digest()

    # Final custom-base64 encoding of the 16-byte digest in Apache's byte order.
    encoded = (
        _to64((digest[0] << 16) | (digest[6] << 8) | digest[12], 4)
        + _to64((digest[1] << 16) | (digest[7] << 8) | digest[13], 4)
        + _to64((digest[2] << 16) | (digest[8] << 8) | digest[14], 4)
        + _to64((digest[3] << 16) | (digest[9] << 8) | digest[15], 4)
        + _to64((digest[4] << 16) | (digest[10] << 8) | digest[5], 4)
        + _to64(digest[11], 2)
    )
    return f"$apr1${salt}${encoded}"


def hash_apr1(password: str, *, salt: str | None = None) -> str:
    """Return an ``$apr1$`` hash of ``password`` (random 8-char salt by default)."""
    if salt is None:
        salt = "".join(secrets.choice(_SALT_CHARS) for _ in range(8))
    elif len(salt) > 8:
        raise ValueError("apr1 salt must be at most 8 characters")
    return _crypt_apr1(password, salt)


def verify_apr1(password: str, hashed: str) -> bool:
    """Verify ``password`` against an ``$apr1$`` hash; ``False`` if malformed."""
    parts = hashed.split("$")
    # ["", "apr1", salt, digest]
    if len(parts) != 4 or parts[1] != "apr1":
        return False
    return secrets.compare_digest(_crypt_apr1(password, parts[2]), hashed)


def htpasswd_line(username: str, password_hash: str) -> str:
    """Format one ``username:hash`` line for an htpasswd file."""
    return f"{username}:{password_hash}"


__all__ = ["hash_apr1", "verify_apr1", "htpasswd_line"]
