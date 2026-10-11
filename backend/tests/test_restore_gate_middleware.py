"""The API stands still while a restore replaces the database.

Every write during a restore would land in a database that is about to be
replaced, or in one mid-replacement. Two paths stay open: the health check, so
the container is not killed, and the restore status, so the admin's browser can
watch the restore finish.
"""

from __future__ import annotations

from types import SimpleNamespace

import fakeredis
import fakeredis.aioredis
import pytest
from app.main import create_app
from app.services.backup import gate
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def shared_redis(monkeypatch):
    """One fake store, written by the worker's sync client and read by the
    API's async one — the split the real deployment has."""
    server = fakeredis.FakeServer()
    monkeypatch.setattr(
        "app.main.redis_client", lambda: fakeredis.aioredis.FakeRedis(server=server)
    )
    return fakeredis.FakeStrictRedis(server=server)


@pytest.fixture
def restoring(shared_redis):
    gate.begin_restore(shared_redis, "restoring the database")
    return shared_redis


@pytest.fixture
async def http(restoring):
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://testserver"
    ) as client:
        yield client


async def test_a_write_is_refused_while_a_restore_runs(http) -> None:
    response = await http.post("/api/v1/proxy-hosts", json={})

    assert response.status_code == 503
    assert response.headers["Retry-After"]


async def test_the_restore_status_still_answers(http) -> None:
    # Without this the admin's browser has no way to learn the restore ended.
    response = await http.get("/api/v1/backups/restore-status")

    assert response.status_code != 503


async def test_the_health_check_still_answers(http) -> None:
    # A 503 here would have the orchestrator kill the container mid-restore.
    response = await http.get("/health")

    assert response.status_code == 200


async def test_the_connection_pool_is_dropped_after_a_restore(monkeypatch, shared_redis) -> None:
    """A restore replaces the schema under the API's own pooled connections.

    asyncpg caches a prepared statement per query; after pg_restore drops and
    recreates every table those plans are invalid, and the next requests fail
    with InvalidCachedStatementError — including the status route the blocking
    screen is polling. Measured against a real stack before this existed.
    """
    disposed: list[bool] = []

    async def _dispose() -> None:
        disposed.append(True)

    monkeypatch.setattr("app.main.engine", SimpleNamespace(dispose=_dispose))
    gate.mark_restored(shared_redis)

    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://testserver"
    ) as http:
        await http.get("/api/v1/backups/restore-status")
        await http.get("/api/v1/backups/restore-status")

    # Once for the restore that happened, not on every request afterwards.
    assert disposed == [True]


async def test_the_gate_uses_the_async_redis_client(monkeypatch) -> None:
    """Every API request passes through this; a blocking socket read here is a
    latency tax on the whole product, paid by a feature nobody is using."""
    import app.main as main

    client = main.redis_client()
    try:
        assert "redis.asyncio" in type(client).__module__
    finally:
        await client.aclose()
