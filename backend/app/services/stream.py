"""Stream (TCP/UDP forward) domain services.

CRUD business logic for streams; routes stay thin. No FastAPI imports — callers
pass an :class:`~sqlalchemy.ext.asyncio.AsyncSession` and plain values.

Two failure modes are translated into typed errors so they never surface as a
raw 500:

* ``incoming_port`` is unique — a collision raises :class:`PortConflictError`
  (mapped to 409). We check explicitly so the conflict is distinguishable from a
  bad certificate reference (both would otherwise land as ``IntegrityError``).
* A bad optional ``certificate_id`` raises :class:`InvalidReferenceError` (422).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UpstreamContext
from app.models.stream import Stream
from app.models.upstream import Upstream
from app.services import upstream as upstream_service


class StreamNotFoundError(Exception):
    """Raised when a stream id does not exist."""


class PortConflictError(Exception):
    """Raised when another stream already claims the requested incoming port."""


class InvalidReferenceError(Exception):
    """Raised when a referenced certificate does not exist."""


async def _port_owner(db: AsyncSession, incoming_port: int) -> int | None:
    """Return the id of the stream holding ``incoming_port``, or ``None``."""
    return await db.scalar(select(Stream.id).where(Stream.incoming_port == incoming_port))


async def get_stream(db: AsyncSession, stream_id: int) -> Stream | None:
    """Return the stream or ``None``."""
    return await db.get(Stream, stream_id)


async def list_streams(db: AsyncSession) -> list[Stream]:
    """Return all streams ordered by id."""
    result = await db.execute(select(Stream).order_by(Stream.id))
    return list(result.scalars().all())


async def _assert_pool_usable(db: AsyncSession, upstream_id: int) -> None:
    """The pool must exist and be attachable in the stream context.

    An http-only pool is never rendered into ``stream {}``, so a stream naming
    it would emit a ``server`` block referencing an ``upstream`` that does not
    exist there — an ``nginx -t`` failure that rolls back the whole apply.
    Reported as :class:`InvalidReferenceError`, which the routes answer 422 for.
    """
    pool = await db.get(Upstream, upstream_id)
    if pool is None:
        raise InvalidReferenceError(f"upstream {upstream_id} does not exist")
    try:
        upstream_service.assert_usable_in(pool, UpstreamContext.stream)
    except upstream_service.InvalidPoolConfigError as exc:
        raise InvalidReferenceError(str(exc)) from None


async def create_stream(db: AsyncSession, values: dict[str, Any]) -> Stream:
    """Create a stream.

    Raises :class:`PortConflictError` if ``incoming_port`` is taken, or
    :class:`InvalidReferenceError` if the optional certificate does not exist.
    """
    if await _port_owner(db, values["incoming_port"]) is not None:
        raise PortConflictError(f"incoming_port {values['incoming_port']} is already in use")
    if values.get("upstream_id") is not None:
        await _assert_pool_usable(db, values["upstream_id"])

    stream = Stream(**values)
    db.add(stream)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise InvalidReferenceError(str(exc.orig)) from exc
    await db.refresh(stream)
    return stream


async def update_stream(db: AsyncSession, stream_id: int, changes: dict[str, Any]) -> Stream:
    """Apply a partial update to a stream.

    Raises :class:`StreamNotFoundError` if missing, :class:`PortConflictError`
    if the new port collides with another stream, or
    :class:`InvalidReferenceError` if a changed reference is invalid.
    """
    stream = await get_stream(db, stream_id)
    if stream is None:
        raise StreamNotFoundError(str(stream_id))

    new_port = changes.get("incoming_port")
    if new_port is not None and new_port != stream.incoming_port:
        owner = await _port_owner(db, new_port)
        if owner is not None and owner != stream_id:
            raise PortConflictError(f"incoming_port {new_port} is already in use")

    if changes.get("upstream_id") is not None:
        await _assert_pool_usable(db, changes["upstream_id"])

    for field, value in changes.items():
        setattr(stream, field, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise InvalidReferenceError(str(exc.orig)) from exc
    await db.refresh(stream)
    return stream


async def delete_stream(db: AsyncSession, stream_id: int) -> None:
    """Delete a stream.

    Raises :class:`StreamNotFoundError` if it does not exist.
    """
    stream = await get_stream(db, stream_id)
    if stream is None:
        raise StreamNotFoundError(str(stream_id))
    await db.delete(stream)
    await db.commit()


__all__ = [
    "InvalidReferenceError",
    "PortConflictError",
    "StreamNotFoundError",
    "create_stream",
    "delete_stream",
    "get_stream",
    "list_streams",
    "update_stream",
]
