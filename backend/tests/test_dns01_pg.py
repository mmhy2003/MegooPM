"""DNS-01 paths that need the Postgres-only ``certificates`` table (skipped without a DB).

Runs inside one rolled-back transaction, so nothing persists.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.core.config import settings
from app.db.base import Base
from app.models.certificate import Certificate
from app.models.enums import CertificateProvider
from app.schemas.certificate import CertificateRead
from app.services.certs import dns_credentials as dns_svc
from app.services.certs import service as cert_service
from app.services.certs.acme_client import ChallengeType
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


@pytest.fixture
async def pg_conn() -> AsyncIterator:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        conn = await engine.connect()
    except Exception:  # pragma: no cover - environment without a database
        await engine.dispose()
        pytest.skip("No database reachable at DATABASE_URL")
    trans = await conn.begin()
    await conn.run_sync(Base.metadata.create_all)
    try:
        yield conn
    finally:
        if trans.is_active:
            await trans.rollback()
        await conn.close()
        await engine.dispose()


@pytest.fixture
async def pg_session(pg_conn) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    async with factory() as session:
        yield session


async def _credential(session: AsyncSession):
    return await dns_svc.create_credential(
        session, name="cf", provider="cloudflare", options={"auth_token": "tok-secret"}
    )


async def test_certificate_stores_credential_reference_and_reads_expose_it(
    pg_session: AsyncSession,
) -> None:
    cred = await _credential(pg_session)
    cert = await cert_service.create_letsencrypt_certificate(
        pg_session,
        name="wild",
        domain_names=["*.example.com"],
        challenge=ChallengeType.DNS_01,
        dns_credential_id=cred.id,
        dns_provider=cred.provider,
    )
    await pg_session.flush()

    assert cert.meta["dns_credential_id"] == cred.id
    assert cert.meta["dns_provider"] == "cloudflare"
    read = CertificateRead.model_validate(cert)
    assert (read.challenge, read.dns_provider, read.dns_provider_label) == (
        "dns-01",
        "cloudflare",
        "Cloudflare",
    )

    using = await dns_svc.certificates_using(pg_session, cred.id)
    assert [c.name for c in using] == ["wild"]
    with pytest.raises(dns_svc.CredentialInUseError):
        await dns_svc.delete_credential(pg_session, cred)


async def test_http01_certificate_has_no_dns_fields(pg_session: AsyncSession) -> None:
    cert = await cert_service.create_letsencrypt_certificate(
        pg_session, name="plain", domain_names=["example.com"]
    )
    await pg_session.flush()
    read = CertificateRead.model_validate(cert)
    assert (read.challenge, read.dns_provider, read.dns_provider_label) == (
        "http-01",
        None,
        None,
    )
    assert "dns_credential_id" not in cert.meta


def test_orm_properties_read_meta_without_a_database() -> None:
    cert = Certificate(
        name="x",
        provider=CertificateProvider.letsencrypt,
        meta={"challenge": "dns-01", "dns_provider": "hetzner"},
    )
    assert cert.challenge == "dns-01"
    assert cert.dns_provider == "hetzner"
    assert (
        Certificate(name="y", provider=CertificateProvider.letsencrypt, meta={}).challenge is None
    )
    # Non-ACME certificates never report a challenge, even if meta carries one.
    assert (
        Certificate(
            name="z", provider=CertificateProvider.custom, meta={"challenge": "dns-01"}
        ).challenge
        is None
    )


# --- issuance task ---------------------------------------------------------------

from app.models.enums import CertificateStatus  # noqa: E402
from app.services.certs.acme_client import SelfSignedIssuer  # noqa: E402
from app.services.certs.dns_providers.lexicon_provider import LexiconDnsProvider  # noqa: E402
from app.tasks import certs as cert_tasks  # noqa: E402


async def test_issue_task_passes_the_resolved_provider_to_build_issuer(
    pg_conn, pg_session: AsyncSession, monkeypatch, tmp_path
) -> None:
    cred = await _credential(pg_session)
    cert = await cert_service.create_letsencrypt_certificate(
        pg_session,
        name="wild",
        domain_names=["example.com"],
        challenge=ChallengeType.DNS_01,
        dns_credential_id=cred.id,
        dns_provider=cred.provider,
    )
    await pg_session.commit()  # savepoint inside the rolled-back outer transaction

    captured: dict = {}

    def fake_build_issuer(certificate, *, dns_provider=None):
        captured["provider"] = dns_provider
        return SelfSignedIssuer()

    monkeypatch.setattr(cert_tasks, "build_issuer", fake_build_issuer)
    monkeypatch.setattr(settings, "nginx_certs_dir", str(tmp_path))
    factory = async_sessionmaker(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )

    result = await cert_tasks._issue_async(cert.id, session_factory=factory)

    assert result["issued"] is True, result
    assert isinstance(captured["provider"], LexiconDnsProvider)
    assert captured["provider"].provider_id == "cloudflare"


async def test_issue_task_fails_cleanly_when_the_credential_is_gone(
    pg_conn, pg_session: AsyncSession, monkeypatch, tmp_path
) -> None:
    cert = await cert_service.create_letsencrypt_certificate(
        pg_session,
        name="orphan",
        domain_names=["example.com"],
        challenge=ChallengeType.DNS_01,
        dns_credential_id=999999,
        dns_provider="cloudflare",
    )
    await pg_session.commit()
    monkeypatch.setattr(settings, "nginx_certs_dir", str(tmp_path))
    factory = async_sessionmaker(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )

    result = await cert_tasks._issue_async(cert.id, session_factory=factory)

    assert result["issued"] is False and result["status"] == "failed"
    assert "no longer exists" in result["error"]
    await pg_session.refresh(cert)
    assert cert.status == CertificateStatus.failed
    assert "no longer exists" in cert.meta["last_error"]
