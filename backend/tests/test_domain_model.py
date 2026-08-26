"""Database-backed tests for the MEG-15 core domain model.

These exercise the real schema (relationships, cascade rules, and check
constraints) against Postgres. They require a reachable database — CI provides
one via ``DATABASE_URL``; when no database is reachable the whole module is
skipped so the DB-less smoke suite still runs everywhere.

Each test runs inside a transaction that is rolled back on teardown, so tests
never leave rows behind and can run in any order.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.core.config import settings
from app.db.base import Base
from app.models import (
    AccessList,
    Certificate,
    ProxyHost,
    Stream,
    Upstream,
    UpstreamBackend,
)
from app.models.enums import CertificateProvider, HttpScheme, LoadBalanceMethod
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload


@pytest.fixture
async def db_session() -> AsyncIterator:
    """Yield a transactional AsyncSession bound to a rolled-back connection."""
    engine = create_async_engine(settings.database_url, poolclass=None)
    try:
        conn = await engine.connect()
    except Exception:  # pragma: no cover - environment without a database
        await engine.dispose()
        pytest.skip("No database reachable at DATABASE_URL")

    trans = await conn.begin()
    # Ensure the schema exists for local runs; a no-op when migrations already
    # created it (checkfirst). Rolled back with the surrounding transaction.
    await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=conn, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
    finally:
        await session.close()
        # A constraint-violating flush aborts the transaction itself; only roll
        # back when it is still active to avoid a spurious warning.
        if trans.is_active:
            await trans.rollback()
        await conn.close()
        await engine.dispose()


async def test_proxy_host_references_pool_with_n_backends(db_session) -> None:
    """A proxy host resolves to one upstream pool holding N backends."""
    pool = Upstream(name="web-pool", lb_method=LoadBalanceMethod.least_conn)
    pool.backends = [
        UpstreamBackend(host="10.0.0.1", port=8080, weight=5),
        UpstreamBackend(host="10.0.0.2", port=8080),
        UpstreamBackend(host="10.0.0.3", port=8080, backup=True),
    ]
    host = ProxyHost(
        domain_names=["app.example.com"],
        upstream=pool,
        forward_scheme=HttpScheme.http,
    )
    db_session.add(host)
    await db_session.flush()
    host_id = host.id
    db_session.expire_all()

    loaded = (
        await db_session.execute(
            select(ProxyHost)
            .where(ProxyHost.id == host_id)
            .options(selectinload(ProxyHost.upstream).selectinload(Upstream.backends))
        )
    ).scalar_one()
    assert loaded.upstream.lb_method is LoadBalanceMethod.least_conn
    assert len(loaded.upstream.backends) == 3
    assert {b.host for b in loaded.upstream.backends} == {
        "10.0.0.1",
        "10.0.0.2",
        "10.0.0.3",
    }


async def test_deleting_pool_cascades_to_backends(db_session) -> None:
    """Deleting an upstream removes its backends via ON DELETE CASCADE."""
    pool = Upstream(name="cascade-pool")
    pool.backends = [
        UpstreamBackend(host="10.1.0.1", port=80),
        UpstreamBackend(host="10.1.0.2", port=80),
    ]
    db_session.add(pool)
    await db_session.flush()
    pool_id = pool.id

    await db_session.delete(pool)
    await db_session.flush()

    remaining = await db_session.scalar(
        select(func.count())
        .select_from(UpstreamBackend)
        .where(UpstreamBackend.upstream_id == pool_id)
    )
    assert remaining == 0


async def test_referenced_pool_cannot_be_deleted(db_session) -> None:
    """A pool referenced by a proxy host is protected by ON DELETE RESTRICT."""
    pool = Upstream(name="restrict-pool")
    pool.backends = [UpstreamBackend(host="10.2.0.1", port=80)]
    host = ProxyHost(domain_names=["restrict.example.com"], upstream=pool)
    db_session.add(host)
    await db_session.flush()

    with pytest.raises(IntegrityError):
        # Raw DELETE to hit the DB-level FK rather than ORM cascade handling.
        await db_session.execute(text("DELETE FROM upstreams WHERE id = :id"), {"id": pool.id})
        await db_session.flush()


async def test_certificate_delete_sets_null_on_hosts(db_session) -> None:
    """Deleting a certificate nulls the reference on dependent hosts."""
    pool = Upstream(name="cert-pool")
    pool.backends = [UpstreamBackend(host="10.3.0.1", port=443)]
    cert = Certificate(name="wildcard", provider=CertificateProvider.letsencrypt)
    host = ProxyHost(domain_names=["secure.example.com"], upstream=pool, certificate=cert)
    db_session.add(host)
    await db_session.flush()
    host_id = host.id

    await db_session.delete(cert)
    await db_session.flush()
    db_session.expire_all()

    reloaded = await db_session.get(ProxyHost, host_id)
    assert reloaded is not None
    assert reloaded.certificate_id is None


async def test_access_list_link_optional(db_session) -> None:
    """A proxy host may attach an access list; it is optional."""
    pool = Upstream(name="acl-pool")
    pool.backends = [UpstreamBackend(host="10.4.0.1", port=80)]
    acl = AccessList(name="office-only")
    host = ProxyHost(domain_names=["acl.example.com"], upstream=pool, access_list=acl)
    db_session.add(host)
    await db_session.flush()

    assert host.access_list_id == acl.id


async def test_stream_requires_a_protocol(db_session) -> None:
    """A stream with neither TCP nor UDP violates the check constraint."""
    stream = Stream(
        incoming_port=9000,
        forward_host="10.5.0.1",
        forward_port=9000,
        tcp_forwarding=False,
        udp_forwarding=False,
    )
    db_session.add(stream)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_backend_port_range_enforced(db_session) -> None:
    """An out-of-range backend port violates the check constraint."""
    pool = Upstream(name="bad-port-pool")
    pool.backends = [UpstreamBackend(host="10.6.0.1", port=70000)]
    db_session.add(pool)
    with pytest.raises(IntegrityError):
        await db_session.flush()
