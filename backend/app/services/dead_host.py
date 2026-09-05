"""Dead (404) host domain services.

CRUD business logic for dead hosts; routes stay thin. No FastAPI imports —
callers pass an :class:`~sqlalchemy.ext.asyncio.AsyncSession` and plain values.

A bad optional ``certificate_id`` is translated into a typed error the API maps
to 422 rather than letting a raw database ``IntegrityError``/500 surface.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dead_host import DeadHost


class DeadHostNotFoundError(Exception):
    """Raised when a dead-host id does not exist."""


class InvalidReferenceError(Exception):
    """Raised when a referenced certificate does not exist."""


async def get_dead_host(db: AsyncSession, host_id: int) -> DeadHost | None:
    """Return the dead host or ``None``."""
    return await db.get(DeadHost, host_id)


async def list_dead_hosts(db: AsyncSession) -> list[DeadHost]:
    """Return all dead hosts ordered by id."""
    result = await db.execute(select(DeadHost).order_by(DeadHost.id))
    return list(result.scalars().all())


async def create_dead_host(db: AsyncSession, values: dict[str, Any]) -> DeadHost:
    """Create a dead host.

    Raises :class:`InvalidReferenceError` if the optional certificate does not
    exist.
    """
    host = DeadHost(**values)
    db.add(host)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise InvalidReferenceError(str(exc.orig)) from exc
    await db.refresh(host)
    return host


async def update_dead_host(db: AsyncSession, host_id: int, changes: dict[str, Any]) -> DeadHost:
    """Apply a partial update to a dead host.

    Raises :class:`DeadHostNotFoundError` if missing or
    :class:`InvalidReferenceError` if a changed reference is invalid.
    """
    host = await get_dead_host(db, host_id)
    if host is None:
        raise DeadHostNotFoundError(str(host_id))

    for field, value in changes.items():
        setattr(host, field, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise InvalidReferenceError(str(exc.orig)) from exc
    await db.refresh(host)
    return host


async def delete_dead_host(db: AsyncSession, host_id: int) -> None:
    """Delete a dead host.

    Raises :class:`DeadHostNotFoundError` if it does not exist.
    """
    host = await get_dead_host(db, host_id)
    if host is None:
        raise DeadHostNotFoundError(str(host_id))
    await db.delete(host)
    await db.commit()


__all__ = [
    "DeadHostNotFoundError",
    "InvalidReferenceError",
    "create_dead_host",
    "delete_dead_host",
    "get_dead_host",
    "list_dead_hosts",
    "update_dead_host",
]
