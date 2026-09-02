"""The loader wiring for default-site-over-TLS, against real rows.

The name arithmetic is unit-tested in ``test_nginx_default_tls.py``; this
covers the *wiring* — that ``load_desired_state`` actually consults it, and
that disabling a host is what moves its name from claimed to covered. That is
the reported bug end to end, and no pure test can reach it.

Skipped without Postgres: ``proxy_hosts.domain_names`` is an ARRAY the SQLite
test engine cannot render.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.core.config import settings
from app.db.base import Base
from app.models.certificate import Certificate
from app.models.enums import CertificateProvider, CertificateStatus
from app.models.proxy_host import ProxyHost
from app.models.upstream import Upstream, UpstreamBackend
from app.services.nginx.loader import load_desired_state
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def pg_session() -> AsyncIterator[AsyncSession]:
    """A session in one rolled-back transaction, so nothing is ever committed."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        conn = await engine.connect()
    except Exception:  # pragma: no cover - environment without a database
        await engine.dispose()
        pytest.skip("No database reachable at DATABASE_URL")

    trans = await conn.begin()
    await conn.run_sync(Base.metadata.create_all)
    session = AsyncSession(bind=conn, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        if trans.is_active:
            await trans.rollback()
        await conn.close()
        await engine.dispose()


async def _certificate(session: AsyncSession) -> Certificate:
    cert = Certificate(
        name="wildcard",
        provider=CertificateProvider.letsencrypt,
        status=CertificateStatus.active,
        domain_names=["shop.example.com"],
    )
    session.add(cert)
    await session.flush()
    return cert


async def _host(session: AsyncSession, cert: Certificate, *, enabled: bool) -> ProxyHost:
    # The pool needs a backend: the loader drops a host whose pool is empty
    # rather than emit an invalid block, and a dropped host claims no name.
    pool = Upstream(name=f"pool-{enabled}")
    pool.backends = [UpstreamBackend(host="10.0.0.1", port=8080)]
    session.add(pool)
    await session.flush()
    host = ProxyHost(
        domain_names=["shop.example.com"],
        upstream_id=pool.id,
        certificate_id=cert.id,
        enabled=enabled,
    )
    session.add(host)
    await session.flush()
    return host


async def test_an_enabled_host_keeps_its_own_name(pg_session: AsyncSession) -> None:
    """The catch-all must never take a name a working host is serving."""
    cert = await _certificate(pg_session)
    await _host(pg_session, cert, enabled=True)

    state = await load_desired_state(pg_session, certs_dir="/data/certs")

    assert state.default_tls == ()


async def test_disabling_the_host_covers_its_name(pg_session: AsyncSession) -> None:
    """The reported bug: HTTPS to a disabled host reached an unrelated site."""
    cert = await _certificate(pg_session)
    await _host(pg_session, cert, enabled=False)

    state = await load_desired_state(pg_session, certs_dir="/data/certs")

    assert len(state.default_tls) == 1
    spec = state.default_tls[0]
    assert spec.server_names == ("shop.example.com",)
    assert spec.certificate.id == cert.id
    assert spec.certificate.fullchain_path == f"/data/certs/{cert.id}/fullchain.pem"


async def test_a_pending_certificate_is_never_referenced(pg_session: AsyncSession) -> None:
    """Its files are not on disk; referencing one fails nginx -t and rolls back
    the entire apply for the instance."""
    cert = await _certificate(pg_session)
    cert.status = CertificateStatus.pending
    await _host(pg_session, cert, enabled=False)

    state = await load_desired_state(pg_session, certs_dir="/data/certs")

    assert state.default_tls == ()


async def test_a_host_whose_pool_has_no_backends_is_covered(pg_session: AsyncSession) -> None:
    """The loader drops such a host rather than emit a block with nothing to
    forward to, so it claims no name and has nothing on :443 — meaning HTTPS to
    it reaches a stranger's site today. Covering it is the same improvement
    disabling a host gets.
    """
    cert = await _certificate(pg_session)
    pool = Upstream(name="empty-pool")  # deliberately no backends
    pg_session.add(pool)
    await pg_session.flush()
    pg_session.add(
        ProxyHost(
            domain_names=["shop.example.com"],
            upstream_id=pool.id,
            certificate_id=cert.id,
            enabled=True,
        )
    )
    await pg_session.flush()

    state = await load_desired_state(pg_session, certs_dir="/data/certs")

    assert state.proxy_hosts == ()
    assert state.default_tls[0].server_names == ("shop.example.com",)
