"""The 0031 backfill, against a real Postgres.

Migrations have no test harness in this repo; this one earns an exception
because its whole job is rewriting existing rows. Run the schema up to 0030,
insert one location of each old shape, upgrade, and read the targets back.

``alembic/env.py`` drives an async engine off ``settings.database_url``, so the
run is pointed at a throwaway schema by overriding that URL rather than by
handing Alembic a sync one.
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

SCHEMA = "migration_probe"
#: Captured before the fixture redirects settings at the probe schema.
_BASE_URL = settings.database_url


async def _exec(statements: list[str]) -> list[tuple]:
    """Run statements in the probe schema; return the last one's rows."""
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
    """Point every future connection of this role at ``schema``."""
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
    """A throwaway schema and a configured Alembic; yields an `upgrade` callable."""
    try:
        asyncio.run(_reset_schema())
    except Exception:  # pragma: no cover - environment without a database
        pytest.skip("No database reachable at DATABASE_URL")

    # Every connection this run opens must land in the probe schema, including
    # the one Alembic makes for its own version table. The search path is set
    # on the role rather than in the URL: asyncpg ignores PGOPTIONS (a libpq
    # mechanism), and a query string would have to survive ConfigParser's '%'
    # interpolation. A role setting applies to every new connection.
    asyncio.run(_set_role_search_path(SCHEMA))
    cfg = Config("alembic.ini")

    yield lambda revision: command.upgrade(cfg, revision)

    asyncio.run(_set_role_search_path("public"))
    asyncio.run(_reset_schema())


def test_backfill_reads_the_shape_each_row_already_has(migrated) -> None:
    migrated("0030_crowdsec_updates")
    asyncio.run(
        _exec(
            [
                "INSERT INTO upstreams (id, name, lb_method, context, enabled)"
                " VALUES (1, 'pool-a', 'round_robin', 'http', true)",
                "INSERT INTO proxy_hosts (id, domain_names, upstream_id, forward_scheme, enabled)"
                " VALUES (1, ARRAY['a.example.com'], 1, 'http', true)",
                "INSERT INTO proxy_host_locations (id, proxy_host_id, path, upstream_id,"
                " forward_scheme) VALUES (1, 1, '/pooled/', 1, 'http')",
                "INSERT INTO proxy_host_locations (id, proxy_host_id, path, forward_host,"
                " forward_port, forward_scheme)"
                " VALUES (2, 1, '/single/', 'backend.internal', 8080, 'http')",
            ]
        )
    )

    migrated("0031_location_targets")

    rows = asyncio.run(
        _exec(["SELECT path, target, custom_page_id FROM proxy_host_locations ORDER BY id"])
    )
    assert [tuple(r) for r in rows] == [
        ("/pooled/", "pool", None),
        ("/single/", "host", None),
    ]


def test_the_new_constraint_rejects_a_mismatched_shape(migrated) -> None:
    migrated("0031_location_targets")
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

    # A row that says "pool" while carrying a forward_host used to be
    # unrepresentable only by accident; now the constraint names it.
    with pytest.raises(Exception, match="location_target_exactly_one"):
        asyncio.run(
            _exec(
                [
                    "INSERT INTO proxy_host_locations (proxy_host_id, path, target,"
                    " upstream_id, forward_host, forward_port, forward_scheme)"
                    " VALUES (1, '/x/', 'pool', 1, 'h', 80, 'http')"
                ]
            )
        )

    # default_site needs no target columns at all.
    asyncio.run(
        _exec(
            [
                "INSERT INTO proxy_host_locations (proxy_host_id, path, target, forward_scheme)"
                " VALUES (1, '/fallback/', 'default_site', 'http')"
            ]
        )
    )
