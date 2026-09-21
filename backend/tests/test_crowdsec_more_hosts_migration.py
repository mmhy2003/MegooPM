"""0038 against a real Postgres: existing redirection and 404 hosts come out on.

Uses the same throwaway-schema harness as tests/test_nginx_apply_status_migration.py,
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

SCHEMA = "crowdsec_more_hosts_probe"
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


def test_existing_hosts_are_switched_on(migrated) -> None:
    # The gap closes on deploy: every host that existed before enforces bans.
    migrated("0037_nginx_apply_status")
    asyncio.run(
        _exec(
            [
                "INSERT INTO redirection_hosts (domain_names, forward_domain_name)"
                " VALUES ('{r.example.com}', 't.example.com')",
                "INSERT INTO dead_hosts (domain_names) VALUES ('{d.example.com}')",
            ]
        )
    )

    migrated("0038_crowdsec_more_hosts")

    rows = asyncio.run(
        _exec(
            [
                "SELECT crowdsec_enabled FROM redirection_hosts"
                " UNION ALL SELECT crowdsec_enabled FROM dead_hosts"
            ]
        )
    )
    assert [tuple(r) for r in rows] == [(True,), (True,)]
