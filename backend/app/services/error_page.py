"""Read and replace the error-page bindings.

Only configured codes are stored; the read fills the gaps. That keeps a fresh
install free of rows nobody chose and makes "back to the MegooPM page" a
delete rather than a second way to say default.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.custom_page import CustomPage
from app.models.enums import ErrorPageMode
from app.models.error_page import ERROR_CODES, ErrorPage
from app.schemas.error_page import ErrorPageRead, ErrorPageUpdate


class UnknownCustomPageError(Exception):
    """A referenced page does not exist. The route answers 422."""


async def list_error_pages(db: AsyncSession) -> list[ErrorPageRead]:
    """All eight codes, in order, with the effective setting for each."""
    rows = {row.code: row for row in (await db.scalars(select(ErrorPage))).all()}
    out: list[ErrorPageRead] = []
    for code in ERROR_CODES:
        row = rows.get(code)
        if row is None:
            out.append(ErrorPageRead(code=code, mode=ErrorPageMode.default, custom_page_id=None))
        else:
            out.append(ErrorPageRead.model_validate(row))
    return out


async def replace_error_pages(db: AsyncSession, rows: list[ErrorPageUpdate]) -> list[ErrorPageRead]:
    """Replace the whole set. Codes set to 'default' lose their row."""
    wanted = {row.code: row for row in rows if row.mode is ErrorPageMode.custom_page}

    ids = {row.custom_page_id for row in wanted.values() if row.custom_page_id is not None}
    if ids:
        found = set((await db.scalars(select(CustomPage.id).where(CustomPage.id.in_(ids)))).all())
        missing = {
            code: row.custom_page_id
            for code, row in wanted.items()
            if row.custom_page_id not in found
        }
        if missing:
            # Named by code: the card shows eight rows, and "a page is missing"
            # would not say which one to fix.
            detail = ", ".join(f"{code} (page {page})" for code, page in sorted(missing.items()))
            raise UnknownCustomPageError(f"No such page for: {detail}")

    await db.execute(delete(ErrorPage))
    for code, row in sorted(wanted.items()):
        db.add(ErrorPage(code=code, mode=row.mode, custom_page_id=row.custom_page_id))
    await db.commit()
    return await list_error_pages(db)


__all__ = ["UnknownCustomPageError", "list_error_pages", "replace_error_pages"]
