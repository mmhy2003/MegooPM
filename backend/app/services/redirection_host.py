"""Redirection-host domain services.

CRUD business logic for redirection hosts; routes stay thin. No FastAPI imports
— callers pass an :class:`~sqlalchemy.ext.asyncio.AsyncSession` and plain values.

A bad optional ``certificate_id`` is translated into a typed error the API maps
to 422 rather than letting a raw database ``IntegrityError``/500 surface.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.redirection_host import RedirectionHost


class RedirectionHostNotFoundError(Exception):
    """Raised when a redirection-host id does not exist."""


class InvalidReferenceError(Exception):
    """Raised when a referenced certificate does not exist."""


async def get_redirection_host(db: AsyncSession, host_id: int) -> RedirectionHost | None:
    """Return the redirection host or ``None``."""
    return await db.get(RedirectionHost, host_id)


async def list_redirection_hosts(db: AsyncSession) -> list[RedirectionHost]:
    """Return all redirection hosts ordered by id."""
    result = await db.execute(select(RedirectionHost).order_by(RedirectionHost.id))
    return list(result.scalars().all())


async def create_redirection_host(db: AsyncSession, values: dict[str, Any]) -> RedirectionHost:
    """Create a redirection host.

    Raises :class:`InvalidReferenceError` if the optional certificate does not
    exist.
    """
    host = RedirectionHost(**values)
    db.add(host)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise InvalidReferenceError(str(exc.orig)) from exc
    await db.refresh(host)
    return host


async def update_redirection_host(
    db: AsyncSession, host_id: int, changes: dict[str, Any]
) -> RedirectionHost:
    """Apply a partial update to a redirection host.

    Raises :class:`RedirectionHostNotFoundError` if missing or
    :class:`InvalidReferenceError` if a changed reference is invalid.
    """
    host = await get_redirection_host(db, host_id)
    if host is None:
        raise RedirectionHostNotFoundError(str(host_id))

    for field, value in changes.items():
        setattr(host, field, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise InvalidReferenceError(str(exc.orig)) from exc
    await db.refresh(host)
    return host


async def delete_redirection_host(db: AsyncSession, host_id: int) -> None:
    """Delete a redirection host.

    Raises :class:`RedirectionHostNotFoundError` if it does not exist.
    """
    host = await get_redirection_host(db, host_id)
    if host is None:
        raise RedirectionHostNotFoundError(str(host_id))
    await db.delete(host)
    await db.commit()


__all__ = [
    "InvalidReferenceError",
    "RedirectionHostNotFoundError",
    "create_redirection_host",
    "delete_redirection_host",
    "get_redirection_host",
    "list_redirection_hosts",
    "update_redirection_host",
]
