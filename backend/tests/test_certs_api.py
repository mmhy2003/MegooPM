"""RBAC + validation-gating tests for the certificate endpoints.

Auth/RBAC and upload validation run before the handler touches the Postgres-only
``certificates`` table, so these assert the security contract without that table
(mirroring ``test_nginx_api``). The DB round-trip is covered in
``test_certs_service_pg`` (Postgres) and the service is unit-tested elsewhere.
"""

from __future__ import annotations

from httpx import AsyncClient

from tests._cert_helpers import generate_key, key_to_pem, make_self_signed

BASE = "/api/v1/certificates"


async def test_list_requires_authentication(db_client: AsyncClient) -> None:
    resp = await db_client.get(BASE)
    assert resp.status_code == 401


async def test_list_forbidden_for_non_admin(db_client: AsyncClient, member_token: str) -> None:
    resp = await db_client.get(BASE, headers={"Authorization": f"Bearer {member_token}"})
    assert resp.status_code == 403


async def test_custom_upload_requires_admin(db_client: AsyncClient, member_token: str) -> None:
    resp = await db_client.post(
        f"{BASE}/custom",
        headers={"Authorization": f"Bearer {member_token}"},
        json={"name": "x", "certificate_pem": "a", "private_key_pem": "b"},
    )
    assert resp.status_code == 403


async def test_letsencrypt_request_requires_admin(
    db_client: AsyncClient, member_token: str
) -> None:
    resp = await db_client.post(
        f"{BASE}/letsencrypt",
        headers={"Authorization": f"Bearer {member_token}"},
        json={"name": "x", "domain_names": ["example.com"]},
    )
    assert resp.status_code == 403


async def test_delete_requires_admin(db_client: AsyncClient, member_token: str) -> None:
    resp = await db_client.delete(f"{BASE}/1", headers={"Authorization": f"Bearer {member_token}"})
    assert resp.status_code == 403


async def test_custom_upload_rejects_mismatched_key(
    db_client: AsyncClient, admin_token: str
) -> None:
    cert_pem, _ = make_self_signed(["example.com"])
    wrong_key = key_to_pem(generate_key())
    resp = await db_client.post(
        f"{BASE}/custom",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "mine",
            "certificate_pem": cert_pem,
            "private_key_pem": wrong_key,
        },
    )
    assert resp.status_code == 422
    assert "does not match" in resp.json()["detail"]


async def test_custom_upload_rejects_garbage(db_client: AsyncClient, admin_token: str) -> None:
    resp = await db_client.post(
        f"{BASE}/custom",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "mine", "certificate_pem": "garbage", "private_key_pem": "garbage"},
    )
    assert resp.status_code == 422


# --- DNS-01 credential gating (validated before the certificates table is touched) ---


async def test_dns01_requires_a_credential(db_client: AsyncClient, admin_token: str) -> None:
    resp = await db_client.post(
        f"{BASE}/letsencrypt",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "w", "domain_names": ["*.example.com"], "challenge": "dns-01"},
    )
    assert resp.status_code == 422
    assert "dns_credential_id" in resp.json()["detail"]


async def test_dns01_rejects_unknown_credential(db_client: AsyncClient, admin_token: str) -> None:
    resp = await db_client.post(
        f"{BASE}/letsencrypt",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "w",
            "domain_names": ["*.example.com"],
            "challenge": "dns-01",
            "dns_credential_id": 999999,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Unknown DNS credential"


async def test_http01_rejects_a_credential(db_client: AsyncClient, admin_token: str) -> None:
    resp = await db_client.post(
        f"{BASE}/letsencrypt",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "w", "domain_names": ["example.com"], "dns_credential_id": 1},
    )
    assert resp.status_code == 422
    assert "dns-01" in resp.json()["detail"]
