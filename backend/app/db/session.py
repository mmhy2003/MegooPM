"""Async SQLAlchemy engine and session management.

Exposes a module-level async engine and sessionmaker, plus a ``get_session``
FastAPI dependency that yields a request-scoped ``AsyncSession`` and guarantees
it is closed.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_pre_ping=True,
    future=True,
)

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a request-scoped async database session.

    Use as a FastAPI dependency:

        async def handler(db: AsyncSession = Depends(get_session)):
            ...
    """
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
