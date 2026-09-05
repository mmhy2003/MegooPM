"""On-disk storage for certificate material on the shared certs volume.

Every certificate owns a directory ``{certs_dir}/{cert_id}/`` holding:

* ``fullchain.pem`` — leaf certificate followed by the intermediate chain
  (what nginx's ``ssl_certificate`` wants).
* ``privkey.pem``   — the private key (``ssl_certificate_key``); mode ``0600``.
* ``chain.pem``     — the intermediate chain alone (optional, for OCSP stapling).

Private key material lives here on the shared volume, **never** in the database
(the ``certificates.meta`` column is documented as key-free). Writes are atomic
(write-temp-then-rename) so a concurrent nginx reload never observes a partial
file, and key files are created with restrictive permissions.

The ACME account key is stored alongside, under ``{certs_dir}/_acme/``, keyed by
a hash of the directory URL + account email so staging and production accounts
never collide.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

FULLCHAIN_NAME = "fullchain.pem"
PRIVKEY_NAME = "privkey.pem"
CHAIN_NAME = "chain.pem"

# Directory (relative to certs_dir) holding ACME account keys.
_ACME_DIR = "_acme"


def cert_dir(certs_dir: str, cert_id: int) -> Path:
    """Absolute directory holding one certificate's material."""
    return Path(certs_dir) / str(cert_id)


def fullchain_path(certs_dir: str, cert_id: int) -> Path:
    return cert_dir(certs_dir, cert_id) / FULLCHAIN_NAME


def privkey_path(certs_dir: str, cert_id: int) -> Path:
    return cert_dir(certs_dir, cert_id) / PRIVKEY_NAME


def _atomic_write(path: Path, data: str, *, mode: int) -> None:
    """Write ``data`` to ``path`` atomically with permission ``mode``.

    The temp file is created in the destination directory (same filesystem) so
    ``os.replace`` is a true atomic rename, and its mode is set *before* the
    rename so the final file never briefly exists world-readable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)  # honour mode even if umask stripped bits on create
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def write_material(
    certs_dir: str,
    cert_id: int,
    *,
    fullchain_pem: str,
    privkey_pem: str,
    chain_pem: str | None = None,
) -> None:
    """Persist a certificate's fullchain, private key, and optional chain.

    ``fullchain.pem`` is world-readable (``0644``) — nginx and operators read
    it. ``privkey.pem`` is ``0600`` — only the owner may read the key.
    """
    directory = cert_dir(certs_dir, cert_id)
    directory.mkdir(parents=True, exist_ok=True)
    _atomic_write(directory / FULLCHAIN_NAME, fullchain_pem, mode=0o644)
    _atomic_write(directory / PRIVKEY_NAME, privkey_pem, mode=0o600)
    if chain_pem is not None:
        _atomic_write(directory / CHAIN_NAME, chain_pem, mode=0o644)


def read_fullchain(certs_dir: str, cert_id: int) -> str:
    return fullchain_path(certs_dir, cert_id).read_text()


def material_exists(certs_dir: str, cert_id: int) -> bool:
    """True if both the fullchain and private key are present on disk."""
    return (
        fullchain_path(certs_dir, cert_id).is_file() and privkey_path(certs_dir, cert_id).is_file()
    )


def delete_material(certs_dir: str, cert_id: int) -> None:
    """Remove a certificate's directory and all its material (idempotent)."""
    shutil.rmtree(cert_dir(certs_dir, cert_id), ignore_errors=True)


def account_key_path(certs_dir: str, directory_url: str, email: str | None) -> Path:
    """Stable path for the ACME account key for a directory URL + email pair."""
    digest = hashlib.sha256(f"{directory_url}|{email or ''}".encode()).hexdigest()[:16]
    return Path(certs_dir) / _ACME_DIR / f"account-{digest}.key"


def read_account_key(certs_dir: str, directory_url: str, email: str | None) -> str | None:
    """Return the stored ACME account key PEM, or ``None`` if not yet created."""
    path = account_key_path(certs_dir, directory_url, email)
    return path.read_text() if path.is_file() else None


def write_account_key(certs_dir: str, directory_url: str, email: str | None, key_pem: str) -> None:
    """Persist the ACME account key (mode ``0600``)."""
    _atomic_write(account_key_path(certs_dir, directory_url, email), key_pem, mode=0o600)


__all__ = [
    "CHAIN_NAME",
    "FULLCHAIN_NAME",
    "PRIVKEY_NAME",
    "account_key_path",
    "cert_dir",
    "delete_material",
    "fullchain_path",
    "material_exists",
    "privkey_path",
    "read_account_key",
    "read_fullchain",
    "write_account_key",
    "write_material",
]
