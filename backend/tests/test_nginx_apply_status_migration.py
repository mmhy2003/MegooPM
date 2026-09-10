"""0037 against a real Postgres: the columns exist, start NULL, and round-trip.

Uses the same throwaway-schema harness as tests/test_maintenance_migration.py,
so running it never leaves the shared test database seeded.
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

SCHEMA = "apply_status_probe"
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


def test_an_upgraded_instance_has_no_recorded_apply_yet(migrated) -> None:
    # The banner must show nothing rather than claim a failure it has no
    # evidence for.
    migrated("0037_nginx_apply_status")

    rows = asyncio.run(
        _exec(["SELECT last_apply_ok, last_apply_output FROM cluster_state WHERE id = 1"])
    )

    assert [tuple(r) for r in rows] == [(None, None)]


def test_the_outcome_round_trips(migrated) -> None:
    migrated("0037_nginx_apply_status")
    asyncio.run(
        _exec(
            [
                "UPDATE cluster_state SET last_apply_ok = false,"
                " last_apply_output = 'host not found in upstream', last_apply_at = now()"
                " WHERE id = 1",
            ]
        )
    )

    rows = asyncio.run(
        _exec(["SELECT last_apply_ok, last_apply_output FROM cluster_state WHERE id = 1"])
    )

    assert [tuple(r) for r in rows] == [(False, "host not found in upstream")]
