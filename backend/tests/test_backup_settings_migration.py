"""0039 against a real Postgres: backup settings land with safe defaults.

Uses the same throwaway-schema harness as the other migration tests, so running
it never leaves the shared test database seeded.
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

SCHEMA = "backups_probe"
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


def test_an_upgraded_instance_has_automatic_backups_off(migrated) -> None:
    # Nobody's disk fills up because they upgraded: the schedule starts off,
    # and an admin turns it on when they have chosen a directory.
    migrated("0039_backups")

    rows = asyncio.run(
        _exec(
            [
                "SELECT backup_frequency, backup_keep, last_backup_ok"
                " FROM instance_settings WHERE id = 1"
            ]
        )
    )

    assert [tuple(r) for r in rows] == [("off", 7, None)]


def test_the_audit_log_can_record_a_restore(migrated) -> None:
    migrated("0039_backups")

    rows = asyncio.run(
        _exec(
            [
                "INSERT INTO audit_log (actor, action, object_type, created_at)"
                " VALUES ('admin@example.com', 'restore', 'backup', now())",
                "SELECT action FROM audit_log WHERE object_type = 'backup'",
            ]
        )
    )

    assert [tuple(r) for r in rows] == [("restore",)]
