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
