"""Schemas for the CrowdSec LAPI integration endpoints (MEG-22).

These model the subset of the CrowdSec Local API we expose to the frontend:
active *decisions* (bans/captchas the bouncer enforces), recent *alerts* (what
CrowdSec detected), and the input for pushing a *manual* decision. Field names
mirror the LAPI JSON so mapping stays mechanical.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import CrowdSecJobKind, CrowdSecJobTrigger


class Decision(BaseModel):
    """One active remediation the bouncer enforces (a ban, captcha, …)."""

    id: int | None = None
    origin: str | None = None
    # Remediation kind: ``ban`` | ``captcha`` | ``throttle`` | custom.
    type: str
    # What the value addresses: ``Ip`` | ``Range`` | ``Country`` | ``AS`` | ...
    scope: str
    value: str
    # Human duration string, e.g. ``3h59m59s``.
    duration: str
    scenario: str | None = None
    # ISO-3166 alpha-2, filled by the API from the bundled country database
    # (LAPI does not send it). None for scopes that have no country.
    country: str | None = None
    # When the remediation lifts. LAPI sends this alongside the duration; a
    # duration string alone cannot answer "when does this expire?".
    until: str | None = None
    # A simulated decision is recorded but never enforced.
    simulated: bool | None = None


class AlertSource(BaseModel):
    """Where an alert originated (the offending IP and its geo/AS metadata)."""

    scope: str | None = None
    value: str | None = None
    ip: str | None = None
    cn: str | None = None
    as_name: str | None = Field(default=None, alias="as_name")
    # CrowdSec's geoip-enrich parser populates coordinates alongside the country
    # ("Populate event with geoloc info : as, country, coords, source range"),
    # so the dashboard map plots where attacks really came from instead of
    # shipping a static table of country centroids.
    latitude: float | None = None
    longitude: float | None = None


class MetaPair(BaseModel):
    """One key/value CrowdSec parsed out of a log line.

    ``cscli alerts inspect`` prints these as its Context table at alert level,
    and as the per-event tables under ``-d``.
    """

    key: str | None = None
    value: str | None = None


class AlertEvent(BaseModel):
    """One log line behind an alert, as CrowdSec parsed it."""

    timestamp: str | None = None
    meta: list[MetaPair] = Field(default_factory=list)

    @field_validator("meta", mode="before")
    @classmethod
    def _coerce_null_meta(cls, v: object) -> object:
        return [] if v is None else v


class Alert(BaseModel):
    """A detection event CrowdSec raised, with any decisions it triggered."""

    id: int | None = None
    scenario: str | None = None
    message: str | None = None
    events_count: int | None = None
    source: AlertSource | None = None
    decisions: list[Decision] = Field(default_factory=list)
    created_at: str | None = None
    start_at: str | None = None
    stop_at: str | None = None
    # Everything below is what `cscli alerts inspect` prints and the list view
    # has no room for. The list path leaves them unset rather than absent, so
    # one schema serves both without a second model to keep in step.
    machine_id: str | None = None
    uuid: str | None = None
    simulated: bool | None = None
    remediation: bool | None = None
    #: The Context table: what the scenario matched on.
    meta: list[MetaPair] = Field(default_factory=list)
    #: The individual log lines, only ever populated by the detail fetch.
    events: list[AlertEvent] = Field(default_factory=list)

    @field_validator("meta", "events", mode="before")
    @classmethod
    def _coerce_null_lists(cls, v: object) -> object:
        # Same null-not-empty-list habit as `decisions` above.
        return [] if v is None else v

    @field_validator("decisions", mode="before")
    @classmethod
    def _coerce_null_decisions(cls, v: object) -> object:
        # LAPI sends ``decisions: null`` (not ``[]``) for every decision-less
        # alert — notably all AppSec/WAF detections (``crowdsecurity/vpatch-*``).
        # ``default_factory`` only fires when the key is absent, so coerce the
        # explicit null here to keep the alerts read path from 500ing (MEG-39).
        return [] if v is None else v


class DecisionCreate(BaseModel):
    """Input for pushing a manual decision (operator-initiated ban)."""

    # Only IP/Range bans are exposed via the API for now; both map cleanly onto
    # a single LAPI alert+decision the bouncer enforces immediately.
    scope: Literal["Ip", "Range"] = "Ip"
    value: Annotated[str, Field(min_length=1, description="IP or CIDR range to act on")]
    type: Literal["ban", "captcha", "throttle"] = "ban"
    # Human duration understood by CrowdSec, e.g. ``4h``, ``30m``, ``168h``.
    duration: Annotated[str, Field(min_length=1)] = "4h"
    reason: Annotated[str | None, Field(description="Free-text note stored on the alert")] = None


class Page(BaseModel):
    """Pagination metadata shared by the list responses (MEG-43).

    ``total`` is the count of records matching the current filter (community
    on/off), across all pages; ``items`` holds only the requested slice.
    """

    total: int = 0
    page: int = 1
    page_size: int = 50


class DecisionList(Page):
    """A page of active decisions."""

    items: list[Decision] = Field(default_factory=list)


class AlertList(Page):
    """A page of recent alerts."""

    items: list[Alert] = Field(default_factory=list)


class CrowdSecHealth(BaseModel):
    """Whether the LAPI integration is configured, reachable, and has a machine."""

    configured: bool
    reachable: bool
    # A LAPI machine (watcher) login exists for this deployment. Without it the
    # decision read path still works (bouncer key) but alerts and manual bans do not.
    machine_registered: bool
    lapi_url: str
    detail: str | None = None


class CrowdSecJobRunRead(BaseModel):
    """The last run of one maintenance job."""

    model_config = ConfigDict(from_attributes=True)

    kind: CrowdSecJobKind
    started_at: datetime
    finished_at: datetime | None
    ok: bool
    error: str | None
    trigger: CrowdSecJobTrigger
    restarted: bool
    detail: dict[str, Any]


class CrowdSecMaintenance(BaseModel):
    """What the Updates tab needs in one call."""

    hub: CrowdSecJobRunRead | None
    capi: CrowdSecJobRunRead | None
    reload_configured: bool
    running: dict[str, bool]


__all__ = [
    "CrowdSecJobRunRead",
    "CrowdSecMaintenance",
    "Alert",
    "AlertList",
    "AlertSource",
    "CrowdSecHealth",
    "Decision",
    "DecisionCreate",
    "DecisionList",
    "Page",
]
