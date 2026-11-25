"""0042 against a real Postgres: the table exists and refuses an escaping folder.

Uses the same throwaway-schema harness as tests/test_crowdsec_more_hosts_migration.py,
so running it never leaves the shared test database seeded.
"""

from __future__ import annotations

import asyncio

import pytest
from alembic import command
from alembic.config import Config
from app.core.config import settings
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

SCHEMA = "static_sites_probe"
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
def alembic_cfg():
    try:
        asyncio.run(_reset_schema())
    except Exception:  # pragma: no cover - environment without a database
        pytest.skip("No database reachable at DATABASE_URL")
    asyncio.run(_set_role_search_path(SCHEMA))
    yield Config("alembic.ini")
    asyncio.run(_set_role_search_path("public"))
    asyncio.run(_reset_schema())


def test_table_takes_a_site_with_crowdsec_on(alembic_cfg) -> None:
    command.upgrade(alembic_cfg, "0042_static_sites")

    rows = asyncio.run(
        _exec(
            [
                "INSERT INTO static_sites (domain_names, folder)"
                " VALUES ('{s.example.com}', 'clients/acme')",
                "SELECT folder, crowdsec_enabled, enabled FROM static_sites",
            ]
        )
    )

    assert [tuple(r) for r in rows] == [("clients/acme", True, True)]


@pytest.mark.parametrize("folder", ["../x", "a/../b", ".git", "/etc", "a//b", "trail/"])
def test_database_refuses_an_escaping_folder(alembic_cfg, folder: str) -> None:
    command.upgrade(alembic_cfg, "0042_static_sites")

    with pytest.raises(IntegrityError):
        asyncio.run(
            _exec(
                [
                    "INSERT INTO static_sites (domain_names, folder)"
                    f" VALUES ('{{s.example.com}}', '{folder}')"
                ]
            )
        )


def test_downgrade_drops_the_table(alembic_cfg) -> None:
    command.upgrade(alembic_cfg, "0042_static_sites")
    command.downgrade(alembic_cfg, "0041_waf_body_limit")

    rows = asyncio.run(_exec(["SELECT to_regclass('static_sites')"]))

    assert rows == [(None,)]
