"""Dashboard endpoints (admin-only).

Two endpoints, deliberately not one. The summary is entirely local-database
work; the threat list is the only part that needs CrowdSec, and it is the part
most likely to be slow or unreachable. Keeping them apart means a CrowdSec
outage empties the map instead of blanking the whole page.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AdminUser, SessionDep
from app.api.routes.crowdsec import ClientDep
from app.schemas.dashboard import DashboardSummary
from app.services.dashboard.summary import build_summary

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
