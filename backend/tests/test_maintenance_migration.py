"""The 0035 columns, against a real Postgres.

Mirrors tests/test_location_targets_migration.py: Alembic drives an async
engine off ``settings.database_url``, so the run is pointed at a throwaway
schema by setting the search path on the role — asyncpg ignores PGOPTIONS,
and a URL query would have to survive ConfigParser's '%' interpolation.
"""

from __future__ import annotations

import asyncio

import pytest
from alembic import command
from alembic.config import Config
from app.core.config import settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

SCHEMA = "maintenance_probe"
_BASE_URL = settings.database_url


async def _exec(statements: list[str]) -> list[tuple]:
    engine = create_async_engine(_BASE_URL, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'SET search_path TO "{SCHEMA}"'))
            result = None
            for sql in statements:
                result = await conn.execute(text(sql))
            return list(result.all()) if result is not None and result.returns_rows else []
    finally:
        await engine.dispose()


async def _set_role_search_path(schema: str) -> None:
    engine = create_async_engine(_BASE_URL, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            user = (await conn.execute(text("SELECT current_user"))).scalar_one()
            await conn.execute(text(f'ALTER ROLE "{user}" SET search_path TO {schema}'))
    finally:
        await engine.dispose()


async def _reset_schema() -> None:
    engine = create_async_engine(_BASE_URL, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SET search_path TO public"))
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))
            await conn.execute(text(f'CREATE SCHEMA "{SCHEMA}"'))
    finally:
        await engine.dispose()


@pytest.fixture
def migrated():
    try:
        asyncio.run(_reset_schema())
    except Exception:  # pragma: no cover - environment without a database
        pytest.skip("No database reachable at DATABASE_URL")
    asyncio.run(_set_role_search_path(SCHEMA))
    cfg = Config("alembic.ini")
    yield lambda revision: command.upgrade(cfg, revision)
    asyncio.run(_set_role_search_path("public"))
    asyncio.run(_reset_schema())


def test_a_host_is_not_under_maintenance_by_default(migrated) -> None:
    migrated("0035_maintenance_mode")
    asyncio.run(
        _exec(
            [
                "INSERT INTO upstreams (id, name, lb_method, context, enabled)"
                " VALUES (1, 'pool-a', 'round_robin', 'http', true)",
                "INSERT INTO proxy_hosts (id, domain_names, upstream_id, forward_scheme, enabled)"
                " VALUES (1, ARRAY['a.example.com'], 1, 'http', true)",
            ]
        )
    )

    rows = asyncio.run(
        _exec(["SELECT maintenance_enabled, maintenance_allow FROM proxy_hosts WHERE id = 1"])
    )

    # Switching a feature on must never be something an upgrade does for you.
    assert [tuple(r) for r in rows] == [(False, [])]


def test_the_allow_list_holds_addresses(migrated) -> None:
    migrated("0035_maintenance_mode")
    asyncio.run(
        _exec(
            [
                "INSERT INTO upstreams (id, name, lb_method, context, enabled)"
                " VALUES (1, 'pool-a', 'round_robin', 'http', true)",
                "INSERT INTO proxy_hosts (id, domain_names, upstream_id, forward_scheme, enabled,"
                " maintenance_enabled, maintenance_allow)"
                " VALUES (1, ARRAY['a.example.com'], 1, 'http', true, true,"
                " ARRAY['203.0.113.5', '10.0.0.0/8'])",
            ]
        )
    )

    rows = asyncio.run(_exec(["SELECT maintenance_allow FROM proxy_hosts WHERE id = 1"]))

    assert [tuple(r) for r in rows] == [(["203.0.113.5", "10.0.0.0/8"],)]


def test_the_settings_default_to_the_megoopm_page(migrated) -> None:
    migrated("0035_maintenance_mode")

    rows = asyncio.run(
        _exec(
            [
                "SELECT maintenance_mode, maintenance_page_id,"
                " maintenance_retry_after_minutes FROM instance_settings WHERE id = 1"
            ]
        )
    )

    # A host switched into maintenance must have something to serve on day one.
    assert [tuple(r) for r in rows] == [("megoopm", None, 60)]
