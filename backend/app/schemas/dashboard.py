"""Response shapes for the dashboard.

Every group is independently nullable where its source can fail. A source that
is unreachable empties its own card and nothing else, and ``None`` stays
distinguishable from a zero count — "0 active bans" and "CrowdSec is down" mean
opposite things, and a card that cannot tell them apart misleads the operator
in exactly the situation where accuracy matters.
"""

from __future__ import annotations

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
    """One country's attack count, ready to place on a map.

    Deliberately not CrowdSec-shaped: the request-analytics pipeline will one
    day produce the same type from access logs, and the map component must not
    need changing when it does.
    """

    country: str
    count: int
    # None when no alert for this country carried coordinates: the country is
    # still counted and ranked, it simply cannot be plotted. Dropping it would
    # hide a real attacker from the list to keep the map tidy.
    lat: float | None
    lng: float | None


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
    "ThreatPoint",
    "TrafficSummary",
]
