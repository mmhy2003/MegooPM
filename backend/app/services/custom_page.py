"""Custom-page domain services (reusable HTML response bodies).

CRUD business logic for custom pages; routes stay thin. No FastAPI imports —
callers pass an :class:`~sqlalchemy.ext.asyncio.AsyncSession` and plain values,
mirroring :mod:`app.services.dead_host`.

The unique constraint on ``name`` is enforced by the database, so a collision
surfaces as an :class:`~sqlalchemy.exc.IntegrityError` and is translated into a
typed :class:`DuplicateNameError` the API maps to 409 rather than a raw 500.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.custom_page import CustomPage


class CustomPageNotFoundError(Exception):
    """Raised when a custom-page id does not exist."""


class DuplicateNameError(Exception):
    """Raised when another page already claims the name."""


async def get_custom_page(db: AsyncSession, page_id: int) -> CustomPage | None:
    """Return the page or ``None``."""
    return await db.get(CustomPage, page_id)


async def list_custom_pages(db: AsyncSession) -> list[CustomPage]:
    """Return all pages ordered by name, which is how the index reads."""
    result = await db.execute(select(CustomPage).order_by(CustomPage.name))
    return list(result.scalars().all())


async def create_custom_page(db: AsyncSession, values: dict[str, Any]) -> CustomPage:
    """Create a page. Raises :class:`DuplicateNameError` on a name collision."""
    page = CustomPage(**values)
    db.add(page)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise DuplicateNameError(str(exc.orig)) from exc
    await db.refresh(page)
    return page


async def update_custom_page(db: AsyncSession, page_id: int, changes: dict[str, Any]) -> CustomPage:
    """Apply a partial update to a page."""
    page = await get_custom_page(db, page_id)
    if page is None:
        raise CustomPageNotFoundError(str(page_id))
    for field, value in changes.items():
        setattr(page, field, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise DuplicateNameError(str(exc.orig)) from exc
    await db.refresh(page)
    return page


async def delete_custom_page(db: AsyncSession, page_id: int) -> None:
    """Delete a page."""
    page = await get_custom_page(db, page_id)
    if page is None:
        raise CustomPageNotFoundError(str(page_id))
    await db.delete(page)
    await db.commit()


__all__ = [
    "CustomPageNotFoundError",
    "DuplicateNameError",
    "create_custom_page",
    "delete_custom_page",
    "get_custom_page",
    "list_custom_pages",
    "update_custom_page",
]
