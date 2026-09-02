"""Reading the recorded visitors back for the dashboard.

Read-only aggregation over ``visitor_day``. The window is inclusive of today,
so ``days=1`` means today only — matching how the retention cutoff counts, so
"kept for 30 days" and "show me 30 days" mean the same span.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.visitor_day import VisitorDay
from app.schemas.dashboard import CountryCount, VisitorRow, VisitorSummary


async def load_visitors(
    db: AsyncSession, *, days: int, top: int = 20
) -> VisitorSummary:
    """Aggregate retained rows over the last ``days`` days."""
    since = datetime.now(UTC).date() - timedelta(days=days - 1)
    window = VisitorDay.day >= since

    totals = (
        await db.execute(
            select(
                func.count(func.distinct(VisitorDay.ip)),
                func.coalesce(func.sum(VisitorDay.request_count), 0),
            ).where(window)
        )
    ).one()

    # Countries: rows with no country are excluded from this list, because
    # "unknown" cannot be ranked or mapped meaningfully. They remain in the
    # totals above, so a gap between the two is visible rather than hidden.
    country_rows = (
        await db.execute(
            select(
                VisitorDay.country,
                func.count(func.distinct(VisitorDay.ip)),
                func.sum(VisitorDay.request_count),
            )
            .where(window, VisitorDay.country.is_not(None))
            .group_by(VisitorDay.country)
            .order_by(func.sum(VisitorDay.request_count).desc(), VisitorDay.country)
        )
    ).all()

    # Top IPs summed across days, so a visitor active all week outranks one
    # busy for an hour. Ordered by IP as a tie-break so identical polls do not
    # reshuffle the list under the operator.
    ip_rows = (
        await db.execute(
            select(
                VisitorDay.ip,
                func.max(VisitorDay.country),
                func.sum(VisitorDay.request_count),
                func.max(VisitorDay.last_seen_at),
            )
            .where(window)
            .group_by(VisitorDay.ip)
            .order_by(func.sum(VisitorDay.request_count).desc(), VisitorDay.ip)
            .limit(top)
        )
    ).all()

    return VisitorSummary(
        days=days,
        total_visitors=int(totals[0] or 0),
        total_requests=int(totals[1] or 0),
        countries=[
            CountryCount(country=code, visitors=int(visitors), requests=int(requests))
            for code, visitors, requests in country_rows
        ],
        top_ips=[
            VisitorRow(
                ip=str(ip),
                country=country,
                requests=int(requests),
                last_seen_at=last_seen,
            )
            for ip, country, requests, last_seen in ip_rows
        ],
    )


__all__ = ["load_visitors"]
