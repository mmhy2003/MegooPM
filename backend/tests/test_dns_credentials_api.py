"""Catalog + credential endpoints: RBAC, validation, secrecy, verify, delete guard, audit.

The ``certificates`` table is Postgres-only, so ``certificates_using`` is
patched here; the real JSONB query is covered in ``test_dns01_pg.py``.
"""

from __future__ import annotations

import pytest
from app.models.audit_log import AuditLog
from app.services.certs import dns_credentials as svc
from app.services.certs.dns_providers.lexicon_provider import DnsProviderError
from httpx import AsyncClient
from sqlalchemy import select

PROVIDERS = "/api/v1/dns-providers"
CREDS = "/api/v1/dns-credentials"
CF = {
    "name": "cf-prod",
    "provider": "cloudflare",
    "options": {"auth_token": "cf-secret-token", "zone_id": "z1"},
}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _no_certificates_table(monkeypatch):
    async def none(db, credential_id):
        return []

    monkeypatch.setattr(svc, "certificates_using", none)


async def _create(client: AsyncClient, token: str, body: dict = CF) -> dict:
    resp = await client.post(CREDS, headers=_auth(token), json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- catalog ------------------------------------------------------------------


async def test_catalog_requires_admin(db_client: AsyncClient, member_token: str) -> None:
    assert (await db_client.get(PROVIDERS)).status_code == 401
    assert (await db_client.get(PROVIDERS, headers=_auth(member_token))).status_code == 403


async def test_catalog_lists_providers_with_fields(
    db_client: AsyncClient, admin_token: str
) -> None:
    resp = await db_client.get(PROVIDERS, headers=_auth(admin_token))
    assert resp.status_code == 200, resp.text
    by_id = {p["id"]: p for p in resp.json()}
    assert len(by_id) >= 50 and "localzone" not in by_id
    cf = by_id["cloudflare"]
    assert cf["label"] == "Cloudflare"
    fields = {f["name"]: f for f in cf["fields"]}
    assert fields["auth_token"]["secret"] is True
    assert fields["zone_id"]["secret"] is False


# --- create / read --------------------------------------------------------------


async def test_create_returns_names_of_secrets_never_values(
    db_client: AsyncClient, admin_token: str
) -> None:
    body = await _create(db_client, admin_token)
    assert body["name"] == "cf-prod"
    assert body["provider"] == "cloudflare"
    assert body["provider_label"] == "Cloudflare"
    assert body["options"] == {"zone_id": "z1"}
    assert body["secret_fields"] == ["auth_token"]
    assert body["in_use_by"] == []
    assert "cf-secret-token" not in str(body)

    listed = (await db_client.get(CREDS, headers=_auth(admin_token))).json()
    assert [c["name"] for c in listed] == ["cf-prod"]


async def test_create_denied_to_member(db_client: AsyncClient, member_token: str) -> None:
    resp = await db_client.post(CREDS, headers=_auth(member_token), json=CF)
    assert resp.status_code == 403


async def test_create_validation_errors(db_client: AsyncClient, admin_token: str) -> None:
    unknown = await db_client.post(
        CREDS, headers=_auth(admin_token), json={**CF, "provider": "nope"}
    )
    assert unknown.status_code == 422 and "Unknown DNS provider" in unknown.json()["detail"]
    bad_field = await db_client.post(
        CREDS,
        headers=_auth(admin_token),
        json={**CF, "options": {"auth_token": "t", "bogus": "x"}},
    )
    assert bad_field.status_code == 422 and "Unknown field" in bad_field.json()["detail"]
    no_secret = await db_client.post(
        CREDS, headers=_auth(admin_token), json={**CF, "options": {"zone_id": "z1"}}
    )
    assert no_secret.status_code == 422 and "secret" in no_secret.json()["detail"]
    await _create(db_client, admin_token)
    dup = await db_client.post(CREDS, headers=_auth(admin_token), json=CF)
    assert dup.status_code == 409


# --- update -----------------------------------------------------------------------


async def test_update_renames_and_merges_secrets(
    db_client: AsyncClient, admin_token: str, session_factory
) -> None:
    created = await _create(db_client, admin_token)
    resp = await db_client.patch(
        f"{CREDS}/{created['id']}",
        headers=_auth(admin_token),
        json={"name": "cf-renamed", "options": {"zone_id": "z2", "auth_token": ""}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "cf-renamed"
    assert resp.json()["options"] == {"zone_id": "z2"}
    async with session_factory() as session:
        cred = await svc.get_credential(session, created["id"])
        assert svc.decrypted_options(cred)["auth_token"] == "cf-secret-token"  # blank = keep

    extra = await db_client.patch(
        f"{CREDS}/{created['id']}", headers=_auth(admin_token), json={"provider": "hetzner"}
    )
    assert extra.status_code == 422
    missing = await db_client.patch(
        f"{CREDS}/999999", headers=_auth(admin_token), json={"name": "x"}
    )
    assert missing.status_code == 404


# --- verify -----------------------------------------------------------------------


async def test_verify_reports_provider_outcome(
    db_client: AsyncClient, admin_token: str, monkeypatch
) -> None:
    created = await _create(db_client, admin_token)

    class _Ok:
        def set_txt_record(self, name, value):
            pass

        def remove_txt_record(self, name, value):
            pass

    monkeypatch.setattr(svc, "build_provider", lambda credential: _Ok())
    ok = await db_client.post(
        f"{CREDS}/{created['id']}/verify",
        headers=_auth(admin_token),
        json={"domain": "example.com"},
    )
    assert ok.status_code == 200 and ok.json() == {"ok": True}

    class _Bad:
        def set_txt_record(self, name, value):
            raise DnsProviderError("cloudflare: 401 Unauthorized (token ***)")

        def remove_txt_record(self, name, value):
            pass

    monkeypatch.setattr(svc, "build_provider", lambda credential: _Bad())
    bad = await db_client.post(
        f"{CREDS}/{created['id']}/verify",
        headers=_auth(admin_token),
        json={"domain": "example.com"},
    )
    assert bad.status_code == 400
    assert bad.json()["detail"].startswith("cloudflare:")

    missing = await db_client.post(
        f"{CREDS}/999999/verify", headers=_auth(admin_token), json={"domain": "example.com"}
    )
    assert missing.status_code == 404


# --- delete -----------------------------------------------------------------------


async def test_delete_refuses_while_in_use_then_succeeds(
    db_client: AsyncClient, admin_token: str, monkeypatch
) -> None:
    created = await _create(db_client, admin_token)

    class _Cert:
        def __init__(self, cid, name):
            self.id, self.name = cid, name

    async def in_use(db, credential_id):
        return [_Cert(7, "prod-wildcard"), _Cert(8, "api")]

    monkeypatch.setattr(svc, "certificates_using", in_use)
    listed = (await db_client.get(CREDS, headers=_auth(admin_token))).json()
    assert listed[0]["in_use_by"] == [{"id": 7, "name": "prod-wildcard"}, {"id": 8, "name": "api"}]
    blocked = await db_client.delete(f"{CREDS}/{created['id']}", headers=_auth(admin_token))
    assert blocked.status_code == 409
    assert "prod-wildcard" in blocked.json()["detail"]

    async def free(db, credential_id):
        return []

    monkeypatch.setattr(svc, "certificates_using", free)
    gone = await db_client.delete(f"{CREDS}/{created['id']}", headers=_auth(admin_token))
    assert gone.status_code == 204
    again = await db_client.delete(f"{CREDS}/{created['id']}", headers=_auth(admin_token))
    assert again.status_code == 404


# --- audit --------------------------------------------------------------------------


async def test_mutations_write_scrubbed_audit_rows(
    db_client: AsyncClient, admin_token: str, session_factory
) -> None:
    created = await _create(db_client, admin_token)
    await db_client.patch(
        f"{CREDS}/{created['id']}",
        headers=_auth(admin_token),
        json={"options": {"auth_token": "second"}},
    )
    await db_client.delete(f"{CREDS}/{created['id']}", headers=_auth(admin_token))

    async with session_factory() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.object_type == "dns_credential").order_by(AuditLog.id)
        )
        rows = list(result.scalars())
    assert [r.action for r in rows] == ["create", "update", "delete"]
    assert rows[0].meta == {
        "name": "cf-prod",
        "provider": "cloudflare",
        "fields": ["auth_token", "zone_id"],
    }
    assert rows[1].meta == {"fields": ["auth_token"]}
    assert rows[2].meta == {"name": "cf-prod", "provider": "cloudflare"}
    assert "cf-secret-token" not in str([r.meta for r in rows])
    assert "second" not in str(rows[1].meta)
