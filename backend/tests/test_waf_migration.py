"""The 0041 WAF body-limit columns and job kind, against a real Postgres.

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

SCHEMA = "waf_limit_probe"
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
    yield cfg
    asyncio.run(_set_role_search_path("public"))
    asyncio.run(_reset_schema())


def test_upgrade_adds_the_defaults_and_the_job_kind(migrated) -> None:
    """10 MB and partial on an instance that existed before: uploads keep working."""
    command.upgrade(migrated, "0040_capi_blocked")
    command.upgrade(migrated, "0041_waf_body_limit")

    rows = asyncio.run(
        _exec(
            [
                "SELECT crowdsec_appsec_max_body_bytes, crowdsec_appsec_body_action::text"
                " FROM instance_settings WHERE id = 1"
            ]
        )
    )
    assert [tuple(r) for r in rows] == [(10485760, "partial")]

    asyncio.run(
        _exec(["INSERT INTO crowdsec_job_run (kind, trigger) VALUES ('waf_apply', 'manual')"])
    )
    rows = asyncio.run(_exec(["SELECT kind::text FROM crowdsec_job_run"]))
    assert [tuple(r) for r in rows] == [("waf_apply",)]


def test_downgrade_drops_the_columns(migrated) -> None:
    command.upgrade(migrated, "0041_waf_body_limit")
    command.downgrade(migrated, "0040_capi_blocked")

    rows = asyncio.run(
        _exec(
            [
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = current_schema() AND table_name = 'instance_settings'"
                " AND column_name LIKE 'crowdsec_appsec_%'"
            ]
        )
    )
    assert rows == []
