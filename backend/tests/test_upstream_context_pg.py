"""Pool-context enforcement against real rows (skipped without Postgres).

``proxy_hosts.domain_names`` is a Postgres ``ARRAY``, which the SQLite test
engine cannot render, so anything exercising the proxy-host service has to run
against a real database — the same reason ``test_proxy_hosts_api.py`` is gated
this way. The context rules themselves are unit-tested in
``test_upstream_context.py``; this module covers the *wiring*: that the service
layer actually consults them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.core.config import settings
from app.db.base import Base
from app.models.enums import UpstreamContext
from app.models.upstream import Upstream
from app.services import proxy_host as proxy_host_service
from app.services import stream as stream_service
from app.services import upstream as upstream_service
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
    # DDL is transactional on Postgres, so this rolls back with everything else.
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


async def _pool(session: AsyncSession, name: str, context: UpstreamContext) -> Upstream:
    pool = Upstream(name=name, context=context)
    session.add(pool)
    await session.flush()
    return pool


async def test_proxy_host_cannot_attach_a_stream_only_pool(pg_session: AsyncSession) -> None:
    """A stream-only pool never renders into http{}, so the host would break.

    Surfaced as InvalidReferenceError, which the route already answers 422 for.
    """
    pool = await _pool(pg_session, "db-pool", UpstreamContext.stream)

    with pytest.raises(proxy_host_service.InvalidReferenceError) as err:
        await proxy_host_service.create_proxy_host(
            pg_session, {"domain_names": ["x.example.com"], "upstream_id": pool.id}
        )
    assert "db-pool" in str(err.value)


@pytest.mark.parametrize("context", [UpstreamContext.http, UpstreamContext.both])
async def test_proxy_host_accepts_http_capable_pools(
    pg_session: AsyncSession, context: UpstreamContext
) -> None:
    pool = await _pool(pg_session, f"pool-{context.value}", context)

    host = await proxy_host_service.create_proxy_host(
        pg_session, {"domain_names": ["y.example.com"], "upstream_id": pool.id}
    )
    assert host.upstream_id == pool.id


async def test_narrowing_context_is_blocked_by_a_live_proxy_host(
    pg_session: AsyncSession,
) -> None:
    """Rule 5, end to end: the count comes from the database, not a fixture."""
    pool = await _pool(pg_session, "shared", UpstreamContext.both)
    await proxy_host_service.create_proxy_host(
        pg_session, {"domain_names": ["z.example.com"], "upstream_id": pool.id}
    )

    with pytest.raises(upstream_service.InvalidPoolConfigError) as err:
        await upstream_service.update_upstream(
            pg_session, pool.id, {"context": UpstreamContext.stream}
        )
    assert "1 proxy host(s)" in str(err.value)


async def test_narrowing_context_is_allowed_when_unreferenced(
    pg_session: AsyncSession,
) -> None:
    pool = await _pool(pg_session, "unused", UpstreamContext.both)

    updated = await upstream_service.update_upstream(
        pg_session, pool.id, {"context": UpstreamContext.stream}
    )
    assert updated.context is UpstreamContext.stream


# --- rule 3: a stream may only attach a stream-capable pool -----------------


async def test_stream_cannot_attach_an_http_only_pool(pg_session: AsyncSession) -> None:
    """An http-only pool never renders into stream{}, so the stream would break."""
    pool = await _pool(pg_session, "web-pool", UpstreamContext.http)

    with pytest.raises(stream_service.InvalidReferenceError) as err:
        await stream_service.create_stream(
            pg_session, {"incoming_port": 15432, "upstream_id": pool.id}
        )
    assert "web-pool" in str(err.value)


@pytest.mark.parametrize("context", [UpstreamContext.stream, UpstreamContext.both])
async def test_stream_accepts_stream_capable_pools(
    pg_session: AsyncSession, context: UpstreamContext
) -> None:
    pool = await _pool(pg_session, f"s-{context.value}", context)

    stream = await stream_service.create_stream(
        pg_session, {"incoming_port": 15433, "upstream_id": pool.id}
    )
    assert stream.upstream_id == pool.id
    assert stream.forward_host is None


async def test_stream_rejects_a_missing_pool(pg_session: AsyncSession) -> None:
    with pytest.raises(stream_service.InvalidReferenceError, match="does not exist"):
        await stream_service.create_stream(
            pg_session, {"incoming_port": 15434, "upstream_id": 999999}
        )


async def test_narrowing_context_is_blocked_by_a_live_stream(pg_session: AsyncSession) -> None:
    """Rule 5's streams half — the count must include streams, not just hosts."""
    pool = await _pool(pg_session, "shared-stream", UpstreamContext.both)
    await stream_service.create_stream(pg_session, {"incoming_port": 15435, "upstream_id": pool.id})

    with pytest.raises(upstream_service.InvalidPoolConfigError) as err:
        await upstream_service.update_upstream(
            pg_session, pool.id, {"context": UpstreamContext.http}
        )
    assert "1 stream(s)" in str(err.value)


# --- loader: a stream with nothing to forward to is dropped, not fatal ------


async def test_stream_with_an_empty_pool_is_skipped(pg_session: AsyncSession) -> None:
    """Emitting a server block naming a pool with no servers fails nginx -t.

    Dropping the one broken stream is strictly better than rolling back the
    whole apply for every managed object.
    """
    from app.services.nginx.loader import load_desired_state

    pool = await _pool(pg_session, "empty", UpstreamContext.stream)
    await stream_service.create_stream(pg_session, {"incoming_port": 15440, "upstream_id": pool.id})

    state = await load_desired_state(pg_session)

    assert state.streams == ()
    assert state.stream_upstreams == ()


async def test_stream_with_a_usable_pool_is_included(pg_session: AsyncSession) -> None:
    from app.models.upstream import UpstreamBackend
    from app.services.nginx.loader import load_desired_state

    pool = await _pool(pg_session, "usable", UpstreamContext.stream)
    pg_session.add(UpstreamBackend(upstream_id=pool.id, host="10.0.0.1", port=5432))
    await pg_session.flush()
    await stream_service.create_stream(pg_session, {"incoming_port": 15441, "upstream_id": pool.id})

    state = await load_desired_state(pg_session)

    assert [s.upstream_id for s in state.streams] == [pool.id]
    assert [p.id for p in state.stream_upstreams] == [pool.id]
    # Stream pools must not leak into the http{} set.
    assert state.http_upstreams == ()
