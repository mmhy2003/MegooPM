"""Proxy-host domain services.

CRUD business logic for reverse-proxy hosts; routes stay thin. No FastAPI
imports — callers pass an :class:`~sqlalchemy.ext.asyncio.AsyncSession` and plain
values.

A proxy host must reference an existing upstream pool. We validate that
reference explicitly (returning a typed error the API maps to 422) rather than
letting a bad ``upstream_id`` surface as a raw database ``IntegrityError``/500.
Bad optional references (certificate, access list) are likewise translated, and
the pools behind ``locations`` are checked the same way as the root pool.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import UpstreamContext
from app.models.proxy_host import ProxyHost, ProxyHostLocation
from app.models.upstream import Upstream
from app.services import upstream as upstream_service


class ProxyHostNotFoundError(Exception):
    """Raised when a proxy-host id does not exist."""


class InvalidReferenceError(Exception):
    """Raised when a referenced pool/certificate/access-list does not exist."""


async def _assert_pools_usable(db: AsyncSession, ids: set[int], *, what: str) -> None:
    """Every id must exist *and* be attachable in the http context.

    A stream-only pool is never rendered into ``http {}``, so a host pointing at
    one would emit a ``server`` block naming an ``upstream`` that does not
    exist there — an ``nginx -t`` failure that rolls back the whole apply.
    Reported as :class:`InvalidReferenceError` so the route's existing 422
    covers it, and reusing ``assert_usable_in``'s wording keeps one source of
    truth for the message.
    """
    if not ids:
        return
    pools = (await db.scalars(select(Upstream).where(Upstream.id.in_(ids)))).all()
    missing = ids - {p.id for p in pools}
    if missing:
        raise InvalidReferenceError(
            f"{what} do not exist: " + ", ".join(str(i) for i in sorted(missing))
        )
    for pool in pools:
        try:
            upstream_service.assert_usable_in(pool, UpstreamContext.http)
        except upstream_service.InvalidPoolConfigError as exc:
            raise InvalidReferenceError(str(exc)) from None


def _location_rows(locations: list[dict[str, Any]]) -> list[ProxyHostLocation]:
    return [ProxyHostLocation(**loc) for loc in locations]


async def _check_location_pools(db: AsyncSession, locations: list[dict[str, Any]]) -> None:
    # Skip host-targeted locations: they name no pool to validate.
    ids = {loc["upstream_id"] for loc in locations if loc.get("upstream_id") is not None}
    await _assert_pools_usable(db, ids, what="location upstream(s)")


def _with_locations(stmt):
    return stmt.options(selectinload(ProxyHost.locations))


async def get_proxy_host(db: AsyncSession, host_id: int) -> ProxyHost | None:
    """Return the proxy host (with its locations) or ``None``."""
    return await db.scalar(_with_locations(select(ProxyHost).where(ProxyHost.id == host_id)))


async def list_proxy_hosts(db: AsyncSession) -> list[ProxyHost]:
    """Return all proxy hosts (with locations) ordered by id."""
    result = await db.execute(_with_locations(select(ProxyHost)).order_by(ProxyHost.id))
    return list(result.scalars().all())


async def create_proxy_host(db: AsyncSession, values: dict[str, Any]) -> ProxyHost:
    """Create a proxy host.

    Raises :class:`InvalidReferenceError` if the target pool, a location's pool,
    or an optional certificate/access list does not exist.
    """
    # No pool to check when the target is a single backend.
    if values.get("upstream_id") is not None:
        await _assert_pools_usable(db, {values["upstream_id"]}, what="upstream")
    locations = values.get("locations") or []
    await _check_location_pools(db, locations)

    fields = {k: v for k, v in values.items() if k != "locations"}
    host = ProxyHost(**fields, locations=_location_rows(locations))
    db.add(host)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise InvalidReferenceError(str(exc.orig)) from exc
    # Expire so the re-read reloads the collection in relationship order (the
    # identity map would otherwise keep the as-assigned order). Read the id
    # first: an expired attribute would lazy-load synchronously (MissingGreenlet).
    new_id = host.id
    db.expire(host)
    refreshed = await get_proxy_host(db, new_id)
    assert refreshed is not None
    return refreshed


async def update_proxy_host(db: AsyncSession, host_id: int, changes: dict[str, Any]) -> ProxyHost:
    """Apply a partial update to a proxy host.

    ``changes["locations"]`` (when present) replaces the location list in full;
    ``delete-orphan`` removes the old rows. Raises
    :class:`ProxyHostNotFoundError` if missing or :class:`InvalidReferenceError`
    if a changed reference is invalid.
    """
    host = await get_proxy_host(db, host_id)
    if host is None:
        raise ProxyHostNotFoundError(str(host_id))

    new_upstream = changes.get("upstream_id")
    if new_upstream is not None:
        await _assert_pools_usable(db, {new_upstream}, what="upstream")
    locations = changes.get("locations")
    if locations is not None:
        await _check_location_pools(db, locations)

    for field, value in changes.items():
        if field == "locations":
            continue
        setattr(host, field, value)
    if locations is not None:
        host.locations = _location_rows(locations)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise InvalidReferenceError(str(exc.orig)) from exc
    db.expire(host)
    refreshed = await get_proxy_host(db, host_id)
    assert refreshed is not None
    return refreshed


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
