"""One row per distinct visitor IP per day.

Aggregated, not per-request: at 100 req/s a proxy produces ~8.6 million requests
a day, and this table grows with *visitors* instead — thousands of rows rather
than millions.

Bucketed by day so "who visited in the last 24 hours" is answerable and so
pruning is a single ``DELETE``. The counters are summed across flushes and
across nodes, which is why the writer's upsert adds rather than replaces.

These rows are IP addresses — personal data — so they are retained for a bounded
window and pruned; see ``app.tasks.analytics``.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, String, func
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VisitorDay(Base):
    """One visitor's activity on one day."""

    __tablename__ = "visitor_day"

    # INET rather than text: Postgres validates it, indexes it well, and makes
    # future subnet queries possible without a migration.
    ip: Mapped[str] = mapped_column(INET, primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    request_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Null when the address could not be located: an unlocatable visitor is
    # still a visitor, so a failed lookup must never drop the row.
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
