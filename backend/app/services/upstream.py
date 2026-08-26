"""Upstream-pool domain services.

Business logic for pools and their backends; routes stay thin. No FastAPI
imports — callers pass an :class:`~sqlalchemy.ext.asyncio.AsyncSession` and plain
values, mirroring ``app/services/user.py``.

Backends are loaded eagerly (``selectinload``) on every read so the async
session never has to lazy-load a relationship after the request's transaction
has committed. Database-level guarantees (the ``RESTRICT`` on a referenced pool,
the ``(upstream_id, host, port)`` uniqueness constraint) are translated here into
typed exceptions the API layer maps to clean HTTP status codes.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import LoadBalanceMethod
from app.models.upstream import Upstream, UpstreamBackend


class UpstreamNotFoundError(Exception):
    """Raised when an upstream pool id does not exist."""


class BackendNotFoundError(Exception):
    """Raised when a backend id does not exist within the given pool."""


class DuplicateBackendError(Exception):
    """Raised when a (host, port) pair already exists in the pool."""


class UpstreamInUseError(Exception):
    """Raised when deleting a pool still referenced by a proxy host."""


async def get_upstream(db: AsyncSession, upstream_id: int) -> Upstream | None:
    """Return the pool (with its backends) or ``None``."""
    result = await db.execute(
        select(Upstream)
        .where(Upstream.id == upstream_id)
        .options(selectinload(Upstream.backends))
    )
    return result.scalar_one_or_none()


async def list_upstreams(db: AsyncSession) -> list[Upstream]:
    """Return all pools (with backends) ordered by id."""
    result = await db.execute(
        select(Upstream).options(selectinload(Upstream.backends)).order_by(Upstream.id)
    )
    return list(result.scalars().all())


async def create_upstream(
    db: AsyncSession,
    *,
    name: str,
    description: str = "",
    lb_method: LoadBalanceMethod = LoadBalanceMethod.round_robin,
    enabled: bool = True,
    backends: list[dict[str, Any]] | None = None,
) -> Upstream:
    """Create a pool, optionally seeding backends inline.

    Raises :class:`DuplicateBackendError` if two seed backends share a host/port.
    """
    pool = Upstream(
        name=name,
        description=description,
        lb_method=lb_method,
        enabled=enabled,
        backends=[UpstreamBackend(**b) for b in (backends or [])],
    )
    db.add(pool)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise DuplicateBackendError(str(exc.orig)) from exc
    # Re-read with backends eagerly loaded for the response projection.
    refreshed = await get_upstream(db, pool.id)
    assert refreshed is not None
    return refreshed


async def update_upstream(
    db: AsyncSession, upstream_id: int, changes: dict[str, Any]
) -> Upstream:
    """Apply a partial update to a pool's own attributes.

    Raises :class:`UpstreamNotFoundError` if the pool does not exist.
    """
    pool = await get_upstream(db, upstream_id)
    if pool is None:
        raise UpstreamNotFoundError(str(upstream_id))
    for field, value in changes.items():
        setattr(pool, field, value)
    await db.commit()
    refreshed = await get_upstream(db, upstream_id)
    assert refreshed is not None
    return refreshed


async def delete_upstream(db: AsyncSession, upstream_id: int) -> None:
    """Delete a pool (cascading to its backends).

    Raises :class:`UpstreamNotFoundError` if missing, or
    :class:`UpstreamInUseError` if a proxy host still references it (RESTRICT).
    """
    pool = await get_upstream(db, upstream_id)
    if pool is None:
        raise UpstreamNotFoundError(str(upstream_id))
    await db.delete(pool)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise UpstreamInUseError(str(upstream_id)) from exc


async def add_backend(
    db: AsyncSession, upstream_id: int, fields: dict[str, Any]
) -> UpstreamBackend:
    """Add a backend to a pool.

    Raises :class:`UpstreamNotFoundError` if the pool is missing or
    :class:`DuplicateBackendError` on a duplicate (host, port).
    """
    pool = await get_upstream(db, upstream_id)
    if pool is None:
        raise UpstreamNotFoundError(str(upstream_id))
    backend = UpstreamBackend(upstream_id=upstream_id, **fields)
    db.add(backend)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise DuplicateBackendError(str(exc.orig)) from exc
    await db.refresh(backend)
    return backend


async def _get_backend(
    db: AsyncSession, upstream_id: int, backend_id: int
) -> UpstreamBackend | None:
    result = await db.execute(
        select(UpstreamBackend).where(
            UpstreamBackend.id == backend_id,
            UpstreamBackend.upstream_id == upstream_id,
        )
    )
    return result.scalar_one_or_none()


async def update_backend(
    db: AsyncSession, upstream_id: int, backend_id: int, changes: dict[str, Any]
) -> UpstreamBackend:
    """Partially update a backend within a pool.

    Raises :class:`BackendNotFoundError` if the backend is not in the pool, or
    :class:`DuplicateBackendError` if the change collides with another backend.
    """
    backend = await _get_backend(db, upstream_id, backend_id)
    if backend is None:
        raise BackendNotFoundError(str(backend_id))
    for field, value in changes.items():
        setattr(backend, field, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise DuplicateBackendError(str(exc.orig)) from exc
    await db.refresh(backend)
    return backend


async def remove_backend(db: AsyncSession, upstream_id: int, backend_id: int) -> None:
    """Remove a backend from a pool.

    Raises :class:`BackendNotFoundError` if it is not in the pool.
    """
    backend = await _get_backend(db, upstream_id, backend_id)
    if backend is None:
        raise BackendNotFoundError(str(backend_id))
    await db.delete(backend)
    await db.commit()


__all__ = [
    "BackendNotFoundError",
    "DuplicateBackendError",
    "UpstreamInUseError",
    "UpstreamNotFoundError",
    "add_backend",
    "create_upstream",
    "delete_upstream",
    "get_upstream",
    "list_upstreams",
    "remove_backend",
    "update_backend",
    "update_upstream",
]
