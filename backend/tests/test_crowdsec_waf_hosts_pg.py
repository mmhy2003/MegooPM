"""Which hosts the WAF body limit applies to, against a real Postgres.

AppSec is global while CROWDSEC_APPSEC_URL is set, so the answer is every
enabled host that enforces CrowdSec — of every kind — and not the per-host
``crowdsec_appsec_enabled`` marker, which nothing enforces.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.api.routes.crowdsec import _protected_hosts
from app.core.config import settings
from app.db.base import Base
from app.models.dead_host import DeadHost
from app.models.proxy_host import ProxyHost
from app.models.redirection_host import RedirectionHost
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


@pytest.fixture
async def pg_session() -> AsyncIterator[AsyncSession]:
    """One session in one rolled-back transaction (or skip)."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        conn = await engine.connect()
    except Exception:  # pragma: no cover - environment without a database
        await engine.dispose()
        pytest.skip("No database reachable at DATABASE_URL")
    trans = await conn.begin()
    await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    try:
        async with factory() as session:
            yield session
    finally:
        await trans.rollback()
        await conn.close()
        await engine.dispose()


async def test_protected_hosts_are_every_crowdsec_host(pg_session: AsyncSession) -> None:
    pg_session.add_all(
        [
            ProxyHost(
                domain_names=["cloud.example.com", "files.example.com"],
                forward_host="10.0.0.5",
                forward_port=80,
                crowdsec_enabled=True,
                crowdsec_appsec_enabled=False,
            ),
            ProxyHost(
                domain_names=["plain.example.com"],
                forward_host="10.0.0.6",
                forward_port=80,
                crowdsec_enabled=False,
            ),
            RedirectionHost(
                domain_names=["old.example.com"],
                forward_domain_name="new.example.com",
                crowdsec_enabled=True,
                enabled=False,
            ),
            RedirectionHost(
                domain_names=["moved.example.com"],
                forward_domain_name="new.example.com",
                crowdsec_enabled=True,
            ),
            DeadHost(domain_names=["gone.example.com"], crowdsec_enabled=True),
        ]
    )
    await pg_session.flush()

    hosts = await _protected_hosts(pg_session)

    assert [(h.kind, h.name) for h in hosts] == [
        ("dead", "gone.example.com"),
        ("proxy", "cloud.example.com"),
        ("redirection", "moved.example.com"),
    ]
