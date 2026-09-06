"""The 0034 columns, against a real Postgres.

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

SCHEMA = "capi_health_probe"
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


def test_an_instance_that_has_never_been_checked_reads_as_unknown(migrated) -> None:
    """Nullable with no default, on purpose.

    A `false` default would tell every existing instance its credentials had
    been rejected, and a `true` one would tell them all is well without ever
    having asked.
    """
    migrated("0034_capi_credential_health")

    rows = asyncio.run(
        _exec(
            [
                "SELECT crowdsec_capi_status_ok, crowdsec_capi_status_detail,"
                " crowdsec_capi_checked_at FROM instance_settings WHERE id = 1"
            ]
        )
    )

    assert [tuple(r) for r in rows] == [(None, None, None)]


def test_the_columns_hold_a_rejection(migrated) -> None:
    migrated("0034_capi_credential_health")

    asyncio.run(
        _exec(
            [
                "UPDATE instance_settings SET crowdsec_capi_status_ok = false,"
                " crowdsec_capi_status_detail = 'API error: Forbidden',"
                " crowdsec_capi_checked_at = now() WHERE id = 1"
            ]
        )
    )

    rows = asyncio.run(
        _exec(
            [
                "SELECT crowdsec_capi_status_ok, crowdsec_capi_status_detail"
                " FROM instance_settings WHERE id = 1"
            ]
        )
    )
    assert [tuple(r) for r in rows] == [(False, "API error: Forbidden")]
