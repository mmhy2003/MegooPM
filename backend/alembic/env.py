"""Alembic migration environment (async).

Runs migrations against the async database URL from application settings.
``target_metadata`` is ``Base.metadata`` with all models imported, so
``alembic revision --autogenerate`` sees the full schema.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings

# Import models so their tables register on Base.metadata before autogenerate.
import app.models  # noqa: F401
from app.db.base import Base

config = context.config

# Inject the runtime database URL from settings.
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, which mutes every logger
    # alembic.ini does not name. Harmless in the migration container, but
    # it silences the app when a test drives Alembic in-process.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL, no DBAPI)."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode against an async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
