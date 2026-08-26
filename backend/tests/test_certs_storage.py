"""On-disk certificate storage: layout, permissions, atomicity, deletion."""

from __future__ import annotations

import stat

from app.services.certs import storage


def test_write_material_creates_files_with_correct_modes(tmp_path) -> None:
    certs_dir = str(tmp_path)
    storage.write_material(
        certs_dir,
        42,
        fullchain_pem="FULLCHAIN",
        privkey_pem="PRIVKEY",
        chain_pem="CHAIN",
    )

    assert storage.material_exists(certs_dir, 42)
    assert storage.read_fullchain(certs_dir, 42) == "FULLCHAIN"

    key_path = storage.privkey_path(certs_dir, 42)
    fc_path = storage.fullchain_path(certs_dir, 42)
    # Private key is owner-only (0600); fullchain is world-readable (0644).
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(fc_path.stat().st_mode) == 0o644
    assert (tmp_path / "42" / "chain.pem").read_text() == "CHAIN"


def test_write_material_overwrites_atomically(tmp_path) -> None:
    certs_dir = str(tmp_path)
    storage.write_material(certs_dir, 1, fullchain_pem="v1", privkey_pem="k1")
    storage.write_material(certs_dir, 1, fullchain_pem="v2", privkey_pem="k2")

    assert storage.read_fullchain(certs_dir, 1) == "v2"
    # No stray temp files left behind.
    leftovers = [p.name for p in (tmp_path / "1").iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_delete_material_is_idempotent(tmp_path) -> None:
    certs_dir = str(tmp_path)
    storage.write_material(certs_dir, 7, fullchain_pem="x", privkey_pem="y")
    assert storage.material_exists(certs_dir, 7)

    storage.delete_material(certs_dir, 7)
    assert not storage.material_exists(certs_dir, 7)
    # Deleting again does not raise.
    storage.delete_material(certs_dir, 7)


def test_account_key_roundtrip_and_isolation(tmp_path) -> None:
    certs_dir = str(tmp_path)
    url = "https://acme-staging-v02.api.letsencrypt.org/directory"

    assert storage.read_account_key(certs_dir, url, "a@example.com") is None
    storage.write_account_key(certs_dir, url, "a@example.com", "KEYDATA")
    assert storage.read_account_key(certs_dir, url, "a@example.com") == "KEYDATA"

    # A different email hashes to a different path — accounts never collide.
    assert storage.read_account_key(certs_dir, url, "b@example.com") is None
    key_path = storage.account_key_path(certs_dir, url, "a@example.com")
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
