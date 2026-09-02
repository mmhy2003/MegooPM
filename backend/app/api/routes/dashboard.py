"""Dashboard endpoints (admin-only).

Two endpoints, deliberately not one. The summary is entirely local-database
work; the threat list is the only part that needs CrowdSec, and it is the part
most likely to be slow or unreachable. Keeping them apart means a CrowdSec
outage empties the map instead of blanking the whole page.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, SessionDep
from app.api.routes.crowdsec import ClientDep
from app.core.config import settings
from app.schemas.dashboard import DashboardSummary, ThreatPoint, VisitorSummary
from app.services.dashboard.summary import build_summary
from app.services.dashboard.threats import group_by_country
from app.services.dashboard.visitors import load_visitors

router = APIRouter(tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
async def dashboard_summary(
    _admin: AdminUser, db: SessionDep, client: ClientDep
) -> DashboardSummary:
    """Every card's numbers in one payload.

    One request rather than five list endpoints the browser would have to count
    itself — and one shape that a push transport can later deliver unchanged.
    """
    return await build_summary(db, crowdsec_client=client)


# One day's worth. Enough to show a pattern without pulling a backlog that would
# make the map describe last month rather than now.
ALERT_LIMIT = 500


@router.get("/threats", response_model=list[ThreatPoint])
async def dashboard_threats(_admin: AdminUser, client: ClientDep) -> list[ThreatPoint]:
    """Attack origins by country.

    Separate from the summary because it is the only part that needs CrowdSec,
    so an outage empties the map rather than the page. An unreachable CrowdSec
    returns an empty list for the same reason the summary returns null security:
    the caller must be able to render "unavailable" rather than "no attacks".
    """
    try:
        alerts = await client.list_alerts(limit=ALERT_LIMIT)
    except Exception:  # noqa: BLE001 - any failure empties this one panel
        return []
    return group_by_country(alerts)


# Bounded by the retention window: asking for more days than are kept would
# quietly return a shorter span than the caller requested.
DaysArg = Annotated[int, Query(ge=1, le=365, description="Days to summarise")]


@router.get("/visitors", response_model=VisitorSummary)
async def dashboard_visitors(
    _admin: AdminUser, db: SessionDep, days: DaysArg = 1
) -> VisitorSummary:
    """Recorded visitors and countries. Admin-only.

    Inclusive of today, so days=1 is today. Clamped to the retention window,
    because rows older than that have been deleted and a larger window would
    silently describe a shorter one.
    """
    return await load_visitors(db, days=min(days, settings.visitor_retention_days))
