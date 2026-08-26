"""Certificate service round-trip against Postgres (skipped if unavailable).

The ``certificates`` table uses Postgres-only ``ARRAY``/``JSONB`` columns, so the
SQLite test engine can't exercise persistence. This module talks to the dev
Postgres inside a single transaction that is rolled back, so it never commits
data or interferes with concurrent work — it just proves the ORM + service layer
round-trips real certificate material end to end.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.core.config import settings
from app.models.certificate import Certificate
from app.models.enums import CertificateProvider, CertificateStatus
from app.services.certs import service as cert_service
from app.services.certs import storage
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests._cert_helpers import make_self_signed

pytestmark = pytest.mark.asyncio


async def _pg_available() -> bool:
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            # Ensure the MEG-19 status column exists (migration applied).
            await conn.exec_driver_sql("SELECT status FROM certificates LIMIT 0")
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


@pytest.fixture
async def pg_session() -> AsyncIterator[AsyncSession]:
    if not await _pg_available():
        pytest.skip("Postgres (with MEG-19 migration) not available")
    engine = create_async_engine(settings.database_url)
    conn = await engine.connect()
    trans = await conn.begin()
    session = AsyncSession(bind=conn, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


async def test_custom_upload_roundtrip(pg_session: AsyncSession, tmp_path) -> None:
    cert_pem, key_pem = make_self_signed(["svc.example.com", "www.svc.example.com"])
    cert = await cert_service.create_custom_certificate(
        pg_session,
        name="svc",
        certificate_pem=cert_pem,
        private_key_pem=key_pem,
        certs_dir=str(tmp_path),
    )

    assert cert.id is not None
    assert cert.provider == CertificateProvider.custom
    assert cert.status == CertificateStatus.active
    assert cert.domain_names == ["svc.example.com", "www.svc.example.com"]
    assert storage.material_exists(str(tmp_path), cert.id)

    fetched = await cert_service.get_certificate(pg_session, cert.id)
    assert fetched is not None and fetched.expires_on is not None


async def test_letsencrypt_row_is_pending(pg_session: AsyncSession) -> None:
    cert = await cert_service.create_letsencrypt_certificate(
        pg_session, name="le", domain_names=["le.example.com"]
    )
    assert cert.status == CertificateStatus.pending
    assert cert.provider == CertificateProvider.letsencrypt
    assert cert.meta["challenge"] == "http-01"


async def test_delete_removes_row_and_material(pg_session: AsyncSession, tmp_path) -> None:
    cert_pem, key_pem = make_self_signed(["del.example.com"])
    cert = await cert_service.create_custom_certificate(
        pg_session,
        name="del",
        certificate_pem=cert_pem,
        private_key_pem=key_pem,
        certs_dir=str(tmp_path),
    )
    cert_id = cert.id
    assert storage.material_exists(str(tmp_path), cert_id)

    await cert_service.delete_certificate(pg_session, cert_id, certs_dir=str(tmp_path))
    assert await pg_session.get(Certificate, cert_id) is None
    assert not storage.material_exists(str(tmp_path), cert_id)
