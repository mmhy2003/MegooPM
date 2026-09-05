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

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import LoadBalanceMethod, UpstreamContext
from app.models.proxy_host import ProxyHost, ProxyHostLocation
from app.models.stream import Stream
from app.models.upstream import Upstream, UpstreamBackend


class UpstreamNotFoundError(Exception):
    """Raised when an upstream pool id does not exist."""


class BackendNotFoundError(Exception):
    """Raised when a backend id does not exist within the given pool."""


class DuplicateBackendError(Exception):
    """Raised when a (host, port) pair already exists in the pool."""


class UpstreamInUseError(Exception):
    """Raised when deleting a pool still referenced by a proxy host or stream."""


class InvalidPoolConfigError(Exception):
    """A pool's method, context and backends cannot be combined."""


# nginx forbids backup servers with any hashing or random method:
# "The parameter cannot be used along with the hash, ip_hash, and random load
# balancing methods." (``down`` carries no such restriction.)
_NO_BACKUP_METHODS = frozenset(
    {LoadBalanceMethod.hash, LoadBalanceMethod.ip_hash, LoadBalanceMethod.random}
)


def validate_pool_config(
    *, lb_method: LoadBalanceMethod, context: UpstreamContext, has_backup: bool
) -> None:
    """Reject combinations nginx would refuse, before they reach the config.

    Catching these here matters more than it looks: a directive nginx rejects is
    only discovered at ``nginx -t``, which rolls back the *entire* apply for
    every managed object and reports one generic "failed nginx -t" message. The
    operator gets no indication which pool caused it.
    """
    if lb_method is LoadBalanceMethod.ip_hash and context is not UpstreamContext.http:
        raise InvalidPoolConfigError(
            "ip_hash is not supported for TCP/UDP streams. Use hash or least_conn."
        )
    if has_backup and lb_method in _NO_BACKUP_METHODS:
        raise InvalidPoolConfigError(
            f"nginx does not allow backup servers with the {lb_method.value} method."
        )


def assert_usable_in(pool: Upstream, context: UpstreamContext) -> None:
    """Reject attaching a pool somewhere its declared context does not allow."""
    if pool.context is UpstreamContext.both or pool.context is context:
        return
    where = "streams" if context is UpstreamContext.stream else "proxy hosts"
    raise InvalidPoolConfigError(f"Pool '{pool.name}' is not available for {where}.")


def assert_context_change_allowed(
    *, pool_name: str, new_context: UpstreamContext, counts: dict[str, int]
) -> None:
    """Reject narrowing a pool's context out from under live references.

    Widening is always safe. Narrowing is not: the pool would stop rendering
    into the dropped context, and every object still pointing at it would emit
    a ``server`` block naming an ``upstream`` that no longer exists — which
    fails ``nginx -t`` and rolls back the apply on every node.

    ``counts`` comes from :func:`reference_counts`.
    """
    if new_context is UpstreamContext.both:
        return
    if new_context is UpstreamContext.http and counts.get("streams"):
        raise InvalidPoolConfigError(
            f"Pool '{pool_name}' is used by {counts['streams']} stream(s); keep 'stream' or 'both'."
        )
    if new_context is UpstreamContext.stream and counts.get("proxy_hosts"):
        raise InvalidPoolConfigError(
            f"Pool '{pool_name}' is used by {counts['proxy_hosts']} proxy host(s); "
            "keep 'http' or 'both'."
        )


async def reference_counts(db: AsyncSession, upstream_id: int) -> dict[str, int]:
    """How many objects point at this pool, split by nginx context."""
    hosts = await db.scalar(
        select(func.count()).select_from(ProxyHost).where(ProxyHost.upstream_id == upstream_id)
    )
    locations = await db.scalar(
        select(func.count())
        .select_from(ProxyHostLocation)
        .where(ProxyHostLocation.upstream_id == upstream_id)
    )
    streams = await db.scalar(
        select(func.count()).select_from(Stream).where(Stream.upstream_id == upstream_id)
    )
    return {
        "proxy_hosts": int(hosts or 0) + int(locations or 0),
        "streams": int(streams or 0),
    }


async def get_upstream(db: AsyncSession, upstream_id: int) -> Upstream | None:
    """Return the pool (with its backends) or ``None``."""
    result = await db.execute(
        select(Upstream).where(Upstream.id == upstream_id).options(selectinload(Upstream.backends))
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
    context: UpstreamContext = UpstreamContext.http,
    enabled: bool = True,
    backends: list[dict[str, Any]] | None = None,
) -> Upstream:
    """Create a pool, optionally seeding backends inline.

    Raises :class:`DuplicateBackendError` if two seed backends share a host/port,
    or :class:`InvalidPoolConfigError` if the method, context and backends
    cannot be combined.
    """
    validate_pool_config(
        lb_method=lb_method,
        context=context,
        has_backup=any(b.get("backup") for b in (backends or [])),
    )
    pool = Upstream(
        name=name,
        description=description,
        lb_method=lb_method,
        context=context,
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


async def update_upstream(db: AsyncSession, upstream_id: int, changes: dict[str, Any]) -> Upstream:
    """Apply a partial update to a pool's own attributes.

    Raises :class:`UpstreamNotFoundError` if the pool does not exist.
    """
    pool = await get_upstream(db, upstream_id)
    if pool is None:
        raise UpstreamNotFoundError(str(upstream_id))
    # Validate the merged result: a PATCH that only flips lb_method still has to
    # be checked against the backends already on the pool.
    validate_pool_config(
        lb_method=changes.get("lb_method", pool.lb_method),
        context=changes.get("context", pool.context),
        has_backup=any(b.backup for b in pool.backends),
    )
    new_context = changes.get("context", pool.context)
    if new_context is not pool.context:
        assert_context_change_allowed(
            pool_name=pool.name,
            new_context=new_context,
            counts=await reference_counts(db, upstream_id),
        )
    for field, value in changes.items():
        setattr(pool, field, value)
    await db.commit()
    refreshed = await get_upstream(db, upstream_id)
    assert refreshed is not None
    return refreshed


async def delete_upstream(db: AsyncSession, upstream_id: int) -> None:
    """Delete a pool (cascading to its backends).

    Raises :class:`UpstreamNotFoundError` if missing, or
    :class:`UpstreamInUseError` if a proxy host or stream still references it
    (both hold RESTRICT foreign keys).
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
    # The pool's method is unchanged here, but adding a backup server to a
    # hash/random pool is the same illegal pair arrived at from the other side.
    validate_pool_config(
        lb_method=pool.lb_method,
        context=pool.context,
        has_backup=bool(fields.get("backup")) or any(b.backup for b in pool.backends),
    )
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
    if "backup" in changes:
        # Flipping an existing server to backup reaches the illegal pair too.
        pool = await get_upstream(db, upstream_id)
        assert pool is not None
        validate_pool_config(
            lb_method=pool.lb_method,
            context=pool.context,
            has_backup=bool(changes["backup"])
            or any(b.backup for b in pool.backends if b.id != backend_id),
        )
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
