"""Proxy-host domain services.

CRUD business logic for reverse-proxy hosts; routes stay thin. No FastAPI
imports — callers pass an :class:`~sqlalchemy.ext.asyncio.AsyncSession` and plain
values.

A proxy host must reference an existing upstream pool. We validate that
reference explicitly (returning a typed error the API maps to 422) rather than
letting a bad ``upstream_id`` surface as a raw database ``IntegrityError``/500.
Bad optional references (certificate, access list) are likewise translated.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.proxy_host import ProxyHost
from app.models.upstream import Upstream


class ProxyHostNotFoundError(Exception):
    """Raised when a proxy-host id does not exist."""


class InvalidReferenceError(Exception):
    """Raised when a referenced pool/certificate/access-list does not exist."""


async def _upstream_exists(db: AsyncSession, upstream_id: int) -> bool:
    return (
        await db.scalar(select(Upstream.id).where(Upstream.id == upstream_id))
    ) is not None


async def get_proxy_host(db: AsyncSession, host_id: int) -> ProxyHost | None:
    """Return the proxy host or ``None``."""
    return await db.get(ProxyHost, host_id)


async def list_proxy_hosts(db: AsyncSession) -> list[ProxyHost]:
    """Return all proxy hosts ordered by id."""
    result = await db.execute(select(ProxyHost).order_by(ProxyHost.id))
    return list(result.scalars().all())


async def create_proxy_host(db: AsyncSession, values: dict[str, Any]) -> ProxyHost:
    """Create a proxy host.

    Raises :class:`InvalidReferenceError` if the target pool (or an optional
    certificate/access list) does not exist.
    """
    if not await _upstream_exists(db, values["upstream_id"]):
        raise InvalidReferenceError(f"upstream {values['upstream_id']} does not exist")

    host = ProxyHost(**values)
    db.add(host)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise InvalidReferenceError(str(exc.orig)) from exc
    await db.refresh(host)
    return host


async def update_proxy_host(
    db: AsyncSession, host_id: int, changes: dict[str, Any]
) -> ProxyHost:
    """Apply a partial update to a proxy host.

    Raises :class:`ProxyHostNotFoundError` if missing or
    :class:`InvalidReferenceError` if a changed reference is invalid.
    """
    host = await get_proxy_host(db, host_id)
    if host is None:
        raise ProxyHostNotFoundError(str(host_id))

    new_upstream = changes.get("upstream_id")
    if new_upstream is not None and not await _upstream_exists(db, new_upstream):
        raise InvalidReferenceError(f"upstream {new_upstream} does not exist")

    for field, value in changes.items():
        setattr(host, field, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise InvalidReferenceError(str(exc.orig)) from exc
    await db.refresh(host)
    return host


async def delete_proxy_host(db: AsyncSession, host_id: int) -> None:
    """Delete a proxy host.

    Raises :class:`ProxyHostNotFoundError` if it does not exist.
    """
    host = await get_proxy_host(db, host_id)
    if host is None:
        raise ProxyHostNotFoundError(str(host_id))
    await db.delete(host)
    await db.commit()


__all__ = [
    "InvalidReferenceError",
    "ProxyHostNotFoundError",
    "create_proxy_host",
    "delete_proxy_host",
    "get_proxy_host",
    "list_proxy_hosts",
    "update_proxy_host",
]
