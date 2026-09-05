"""Every dashboard number, gathered in one pass.

Only the CrowdSec call is allowed to fail softly. Every other source is the
local database, where a failure is a real error and must not be disguised as an
empty card — an operator seeing "0 certificates expiring" because a query threw
is worse off than one seeing an error.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.certificate import Certificate
from app.models.dead_host import DeadHost
from app.models.enums import CertificateStatus
from app.models.proxy_host import ProxyHost
from app.models.redirection_host import RedirectionHost
from app.models.stream import Stream
from app.schemas.dashboard import (
    CertificateHealth,
    ConfigHealth,
    DashboardSummary,
    InventoryCounts,
    SecuritySummary,
    TrafficSummary,
)
from app.services.cluster.status import compute_cluster_status
from app.services.dashboard.metrics import load_traffic

EXPIRY_WINDOW_DAYS = 30
TOP_SCENARIOS = 5


async def _count(db: AsyncSession, model, *where) -> int:
    stmt = select(func.count()).select_from(model)
    for clause in where:
        stmt = stmt.where(clause)
    return int(await db.scalar(stmt) or 0)


async def _certificates(db: AsyncSession, now: datetime) -> CertificateHealth:
    cutoff = now + timedelta(days=EXPIRY_WINDOW_DAYS)
    return CertificateHealth(
        # Active, inside the window, and not already past it: an expired
        # certificate is counted once, under `expired`, never in both.
        expiring_soon=await _count(
            db,
            Certificate,
            Certificate.status == CertificateStatus.active,
            Certificate.expires_on.is_not(None),
            Certificate.expires_on <= cutoff,
            Certificate.expires_on > now,
        ),
        expired=await _count(db, Certificate, Certificate.status == CertificateStatus.expired),
        failed=await _count(db, Certificate, Certificate.status == CertificateStatus.failed),
        total=await _count(db, Certificate),
    )


async def _inventory(db: AsyncSession) -> InventoryCounts:
    return InventoryCounts(
        proxy_hosts_total=await _count(db, ProxyHost),
        proxy_hosts_enabled=await _count(db, ProxyHost, ProxyHost.enabled.is_(True)),
        redirection_hosts=await _count(db, RedirectionHost),
        dead_hosts=await _count(db, DeadHost),
        streams=await _count(db, Stream),
    )


async def _security(crowdsec_client) -> SecuritySummary | None:
    """``None`` on any failure.

    Broad by design: an unconfigured, unreachable or erroring CrowdSec must all
    produce the same "unavailable" card rather than a zero that reads as "you
    are not being attacked".
    """
    try:
        decisions = await crowdsec_client.list_decisions()
        alerts = await crowdsec_client.list_alerts()
    except Exception:  # noqa: BLE001 - any failure degrades this one card
        return None

    scenarios: dict[str, int] = {}
    for alert in alerts:
        if alert.scenario:
            scenarios[alert.scenario] = scenarios.get(alert.scenario, 0) + 1
    # Count descending, then name ascending, so identical polls do not reshuffle.
    top = sorted(scenarios, key=lambda name: (-scenarios[name], name))[:TOP_SCENARIOS]

    return SecuritySummary(
        active_decisions=len(decisions), alerts_24h=len(alerts), top_scenarios=top
    )


async def build_summary(db: AsyncSession, *, crowdsec_client) -> DashboardSummary:
    """Assemble every card's numbers."""
    now = datetime.now(UTC)
    cluster = await compute_cluster_status(db)
    totals = await load_traffic(db, now=now, stale_after=settings.node_liveness_window_seconds)
    return DashboardSummary(
        certificates=await _certificates(db, now),
        inventory=await _inventory(db),
        # TrafficTotals is the service dataclass, TrafficSummary the response
        # model; this is the only place that maps between them, which keeps the
        # service usable without importing Pydantic.
        traffic=TrafficSummary(
            active_connections=totals.active_connections,
            requests_per_second=totals.requests_per_second,
            reporting_nodes=totals.reporting_nodes,
            stale_nodes=totals.stale_nodes,
        ),
        config=ConfigHealth(
            config_version=cluster.config_version,
            nodes_total=len(cluster.nodes),
            nodes_in_sync=sum(1 for n in cluster.nodes if n.in_sync and not n.stale),
            nodes_stale=sum(1 for n in cluster.nodes if n.stale),
            converged=cluster.converged,
        ),
        security=await _security(crowdsec_client),
    )


__all__ = ["build_summary"]
