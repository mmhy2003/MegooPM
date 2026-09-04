"""The 0032 table and its constraint, against a real Postgres.

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

SCHEMA = "error_page_probe"
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


def test_the_table_starts_empty(migrated) -> None:
    # A fresh install seeds nothing: an absent row *is* the branded default.
    migrated("0032_error_pages")
    rows = asyncio.run(_exec(["SELECT count(*) FROM error_page"]))
    assert rows[0][0] == 0


def test_the_constraint_ties_the_page_to_the_mode(migrated) -> None:
    migrated("0032_error_pages")
    asyncio.run(
        _exec(["INSERT INTO custom_pages (id, name, description, html) VALUES (1, 'p', '', '')"])
    )

    # default + a page, and custom_page without one, are both nonsense.
    for sql in (
        "INSERT INTO error_page (code, mode, custom_page_id) VALUES (404, 'default', 1)",
        "INSERT INTO error_page (code, mode, custom_page_id) VALUES (404, 'custom_page', NULL)",
    ):
        with pytest.raises(Exception, match="error_page_mode_needs_page"):
            asyncio.run(_exec([sql]))

    asyncio.run(
        _exec(
            [
                "INSERT INTO error_page (code, mode, custom_page_id)"
                " VALUES (404, 'custom_page', 1)",
                "INSERT INTO error_page (code, mode, custom_page_id) VALUES (502, 'default', NULL)",
            ]
        )
    )
    rows = asyncio.run(_exec(["SELECT code, mode FROM error_page ORDER BY code"]))
    assert [tuple(r) for r in rows] == [(404, "custom_page"), (502, "default")]
