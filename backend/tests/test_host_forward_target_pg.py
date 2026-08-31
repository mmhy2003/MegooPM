"""The loader must keep host-targeted proxy hosts in the render.

This is the regression this feature is most likely to introduce. The loader
drops any host whose pool is missing, disabled or empty — correctly, because a
``server`` block naming a non-existent ``upstream`` fails ``nginx -t`` and rolls
back the whole apply. A host-targeted host has no pool *by design*, so leaving
that check unguarded removes it from the config entirely: the site stops being
served and nothing anywhere reports an error.

Postgres-gated for the usual reason — ``proxy_hosts.domain_names`` is an ARRAY.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.core.config import settings
from app.db.base import Base
from app.models.enums import UpstreamContext
from app.models.upstream import Upstream, UpstreamBackend
from app.services import proxy_host as proxy_host_service
from app.services.nginx.loader import load_desired_state
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def pg_session() -> AsyncIterator[AsyncSession]:
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


async def _pool(session: AsyncSession, name: str, *, with_backend: bool) -> Upstream:
    pool = Upstream(name=name, context=UpstreamContext.http)
    session.add(pool)
    await session.flush()
    if with_backend:
        session.add(UpstreamBackend(upstream_id=pool.id, host="10.0.0.1", port=8080))
        await session.flush()
    return pool


async def test_host_targeted_host_is_included(pg_session: AsyncSession) -> None:
    """The whole point: no pool, and still rendered."""
    await proxy_host_service.create_proxy_host(
        pg_session,
        {
            "domain_names": ["literal.example.com"],
            "forward_host": "10.0.0.1",
            "forward_port": 8080,
        },
    )

    state = await load_desired_state(pg_session)

    rendered = [h for h in state.proxy_hosts if "literal.example.com" in h.domain_names]
    assert len(rendered) == 1
    assert rendered[0].forward_host == "10.0.0.1"
    assert rendered[0].upstream_id is None
    # A host that references no pool must contribute none to the http set.
    assert all(p.id is not None for p in state.http_upstreams)


async def test_pool_targeted_host_with_an_empty_pool_is_still_skipped(
    pg_session: AsyncSession,
) -> None:
    """The existing guard must survive: an empty pool is still unrenderable."""
    pool = await _pool(pg_session, "empty-pool", with_backend=False)
    await proxy_host_service.create_proxy_host(
        pg_session, {"domain_names": ["empty.example.com"], "upstream_id": pool.id}
    )

    state = await load_desired_state(pg_session)

    assert not [h for h in state.proxy_hosts if "empty.example.com" in h.domain_names]


async def test_pool_targeted_host_with_a_usable_pool_is_included(
    pg_session: AsyncSession,
) -> None:
    pool = await _pool(pg_session, "good-pool", with_backend=True)
    await proxy_host_service.create_proxy_host(
        pg_session, {"domain_names": ["pooled.example.com"], "upstream_id": pool.id}
    )

    state = await load_desired_state(pg_session)

    rendered = [h for h in state.proxy_hosts if "pooled.example.com" in h.domain_names]
    assert len(rendered) == 1
    assert rendered[0].upstream_id == pool.id
    assert pool.id in {p.id for p in state.http_upstreams}
