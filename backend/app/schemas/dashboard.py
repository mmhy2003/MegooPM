"""Response shapes for the dashboard.

Every group is independently nullable where its source can fail. A source that
is unreachable empties its own card and nothing else, and ``None`` stays
distinguishable from a zero count — "0 active bans" and "CrowdSec is down" mean
opposite things, and a card that cannot tell them apart misleads the operator
in exactly the situation where accuracy matters.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CertificateHealth(BaseModel):
    """Counts an operator would want to act on, not an inventory."""

    expiring_soon: int
    expired: int
    failed: int
    total: int


class InventoryCounts(BaseModel):
    proxy_hosts_total: int
    proxy_hosts_enabled: int
    redirection_hosts: int
    dead_hosts: int
    streams: int


class TrafficSummary(BaseModel):
    """``None`` means no node has reported recently — unknown, not idle."""

    active_connections: int | None
    requests_per_second: float | None
    reporting_nodes: int
    stale_nodes: int


class SecuritySummary(BaseModel):
    active_decisions: int
    alerts_24h: int
    top_scenarios: list[str]


class ConfigHealth(BaseModel):
    config_version: int
    nodes_total: int
    nodes_in_sync: int
    nodes_stale: int
    converged: bool


class ThreatPoint(BaseModel):
    """One country's attack count.

    Position is deliberately absent: the map owns placement, so this and the
    visitor countries arrive in the same shape and a country is always drawn in
    the same spot whichever layer drew it. Sending coordinates that never change
    on every poll bought nothing.
    """

    country: str
    count: int


class CountryCount(BaseModel):
    """One country's share of recorded traffic."""

    country: str
    visitors: int
    requests: int


class VisitorRow(BaseModel):
    """One visitor, summed across the requested window."""

    ip: str
    # None when the address could not be located. The visitor is still listed:
    # hiding them would understate the traffic to keep the map tidy.
    country: str | None
    requests: int
    last_seen_at: datetime


class VisitorSummary(BaseModel):
    """Recorded visitors over a window of days.

    ``total_visitors`` counts every distinct address, including those with no
    country, so the totals and the country breakdown deliberately do not have
    to add up — an operator seeing the difference is seeing unlocated traffic,
    which is real.
    """

    days: int
    total_visitors: int
    total_requests: int
    countries: list[CountryCount]
    top_ips: list[VisitorRow]


class DashboardSummary(BaseModel):
    certificates: CertificateHealth
    inventory: InventoryCounts
    traffic: TrafficSummary
    config: ConfigHealth
    # None when CrowdSec could not be reached. Every other field comes from the
    # local database, where a failure is a real error rather than a soft one.
    security: SecuritySummary | None


__all__ = [
    "CertificateHealth",
    "ConfigHealth",
    "DashboardSummary",
    "InventoryCounts",
    "SecuritySummary",
    "CountryCount",
    "ThreatPoint",
    "VisitorRow",
    "VisitorSummary",
    "TrafficSummary",
]
