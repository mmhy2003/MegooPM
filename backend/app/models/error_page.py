"""What each common HTTP error status is answered with.

One row per *configured* code. A code with no row is served the branded
default, so a fresh install seeds nothing and this table only ever holds what
an operator changed — setting a code back to the default deletes its row.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, Enum, ForeignKey, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.custom_page import CustomPage
from app.models.enums import ErrorPageMode
from app.models.mixins import TimestampMixin

#: The codes the settings page offers and the renderer writes. Closed on
#: purpose: the UI renders exactly these rows, so a ninth would be invisible.
ERROR_CODES: tuple[int, ...] = (400, 401, 403, 404, 500, 502, 503, 504)


class ErrorPage(TimestampMixin, Base):
    __tablename__ = "error_page"
    __table_args__ = (
        CheckConstraint(
            "(mode = 'custom_page' AND custom_page_id IS NOT NULL)"
            " OR (mode = 'default' AND custom_page_id IS NULL)",
            name="error_page_mode_needs_page",
        ),
    )

    # The status code is the identity: at most one binding per code.
    code: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    mode: Mapped[ErrorPageMode] = mapped_column(
        Enum(
            ErrorPageMode,
            name="error_page_mode",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    # RESTRICT, like the default site's: a page in use cannot be deleted.
    custom_page_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("custom_pages.id", ondelete="RESTRICT"), nullable=True, index=True
    )

    custom_page: Mapped[CustomPage] = relationship()


__all__ = ["ERROR_CODES", "ErrorPage"]
