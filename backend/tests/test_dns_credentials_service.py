"""Credential service: encryption at rest, option splitting, merge-on-update, delete guards."""

from __future__ import annotations

import json

import pytest
from app.core.crypto import decrypt_secret
from app.models.certificate import Certificate
from app.services.certs import dns_credentials as svc
from app.services.certs.acme_client import DnsProviderNotConfigured
from app.services.certs.dns_providers.catalog import UnknownDnsProviderError
from app.services.certs.dns_providers.lexicon_provider import LexiconDnsProvider
from sqlalchemy.ext.asyncio import async_sessionmaker


async def _cf(session_factory: async_sessionmaker, name: str = "cf-prod"):
    async with session_factory() as session:
        return await svc.create_credential(
            session,
            name=name,
            provider="cloudflare",
            options={"auth_token": "cf-secret-token", "zone_id": "zone-1", "auth_username": ""},
        )


def test_split_options_by_catalog_secret_flag() -> None:
    public, secret = svc.split_options(
        "cloudflare", {"auth_token": " t ", "zone_id": "z", "auth_username": ""}
    )
    assert public == {"zone_id": "z"}
    assert secret == {"auth_token": "t"}  # trimmed; blank values dropped


def test_split_options_rejects_unknown_provider_and_field() -> None:
    with pytest.raises(UnknownDnsProviderError):
        svc.split_options("nope", {})
    with pytest.raises(ValueError, match="Unknown field"):
        svc.split_options("cloudflare", {"bogus": "x"})


async def test_create_encrypts_secrets_and_keeps_public_options(session_factory) -> None:
    cred = await _cf(session_factory)
    assert cred.options == {"zone_id": "zone-1"}
    assert "cf-secret-token" not in cred.secrets_enc
    assert json.loads(decrypt_secret(cred.secrets_enc)) == {"auth_token": "cf-secret-token"}
    assert svc.secret_field_names(cred) == ["auth_token"]
    assert svc.decrypted_options(cred) == {"zone_id": "zone-1", "auth_token": "cf-secret-token"}


async def test_create_requires_a_secret_field_and_unique_name(session_factory) -> None:
    async with session_factory() as session:
        with pytest.raises(ValueError, match="secret"):
            await svc.create_credential(
                session, name="x", provider="cloudflare", options={"zone_id": "only-public"}
            )
    await _cf(session_factory, "dup")
    async with session_factory() as session:
        with pytest.raises(svc.DuplicateCredentialNameError):
            await svc.create_credential(
                session, name="dup", provider="hetzner", options={"auth_token": "hz"}
            )


async def test_update_merges_secrets_and_replaces_public_options(session_factory) -> None:
    cred = await _cf(session_factory)
    async with session_factory() as session:
        cred = await svc.get_credential(session, cred.id)
        # Blank secret keeps the stored value; public options replaced as given.
        updated = await svc.update_credential(
            session, cred, name="cf-renamed", options={"zone_id": "zone-2", "auth_token": ""}
        )
        assert updated.name == "cf-renamed"
        assert updated.options == {"zone_id": "zone-2"}
        assert svc.decrypted_options(updated)["auth_token"] == "cf-secret-token"
        # A supplied secret replaces the stored one.
        updated = await svc.update_credential(session, updated, options={"auth_token": "new-token"})
        assert svc.decrypted_options(updated) == {"auth_token": "new-token"}


async def test_update_rejects_duplicate_name(session_factory) -> None:
    await _cf(session_factory, "a")
    b = await _cf(session_factory, "b")
    async with session_factory() as session:
        b = await svc.get_credential(session, b.id)
        with pytest.raises(svc.DuplicateCredentialNameError):
            await svc.update_credential(session, b, name="a")


async def test_delete_refuses_while_certificates_use_it(session_factory, monkeypatch) -> None:
    cred = await _cf(session_factory)

    async def fake_using(db, credential_id):
        return [Certificate(name="prod-wildcard"), Certificate(name="api")]

    monkeypatch.setattr(svc, "certificates_using", fake_using)
    async with session_factory() as session:
        cred = await svc.get_credential(session, cred.id)
        with pytest.raises(svc.CredentialInUseError) as excinfo:
            await svc.delete_credential(session, cred)
        assert excinfo.value.certificate_names == ["prod-wildcard", "api"]


async def test_delete_removes_row_when_unused(session_factory, monkeypatch) -> None:
    cred = await _cf(session_factory)

    async def none(db, credential_id):
        return []

    monkeypatch.setattr(svc, "certificates_using", none)
    async with session_factory() as session:
        cred = await svc.get_credential(session, cred.id)
        await svc.delete_credential(session, cred)
        assert await svc.get_credential(session, cred.id) is None


async def test_build_provider_for_resolves_meta_reference(session_factory) -> None:
    cred = await _cf(session_factory)
    async with session_factory() as session:
        cert = Certificate(name="c", meta={"challenge": "dns-01", "dns_credential_id": cred.id})
        provider = await svc.build_provider_for(session, cert)
        assert isinstance(provider, LexiconDnsProvider)
        assert provider.provider_id == "cloudflare"

        with pytest.raises(DnsProviderNotConfigured, match="no DNS credential"):
            await svc.build_provider_for(
                session, Certificate(name="c", meta={"challenge": "dns-01"})
            )
        with pytest.raises(DnsProviderNotConfigured, match="no longer exists"):
            await svc.build_provider_for(
                session, Certificate(name="c", meta={"dns_credential_id": 999999})
            )


async def test_verify_credential_sets_then_removes_a_probe_record(
    session_factory, monkeypatch
) -> None:
    cred = await _cf(session_factory)
    log: list[tuple[str, str, str]] = []

    class _Fake:
        provider_id = "cloudflare"

        def set_txt_record(self, name, value):
            log.append(("set", name, value))

        def remove_txt_record(self, name, value):
            log.append(("remove", name, value))

    monkeypatch.setattr(svc, "build_provider", lambda credential: _Fake())
    svc.verify_credential(cred, "example.com.")

    assert [entry[0] for entry in log] == ["set", "remove"]
    assert log[0][1] == "_megoopm-verify.example.com"
    assert log[0][2].startswith("megoopm-") and log[0][2] == log[1][2]
