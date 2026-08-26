"""Schemas for the CrowdSec LAPI integration endpoints (MEG-22).

These model the subset of the CrowdSec Local API we expose to the frontend:
active *decisions* (bans/captchas the bouncer enforces), recent *alerts* (what
CrowdSec detected), and the input for pushing a *manual* decision. Field names
mirror the LAPI JSON so mapping stays mechanical.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator


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


class AlertSource(BaseModel):
    """Where an alert originated (the offending IP and its geo/AS metadata)."""

    scope: str | None = None
    value: str | None = None
    ip: str | None = None
    cn: str | None = None
    as_name: str | None = Field(default=None, alias="as_name")


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


class DecisionList(BaseModel):
    """A page of active decisions."""

    items: list[Decision] = Field(default_factory=list)
    total: int = 0


class AlertList(BaseModel):
    """A page of recent alerts."""

    items: list[Alert] = Field(default_factory=list)
    total: int = 0


class CrowdSecHealth(BaseModel):
    """Whether the LAPI integration is configured and reachable."""

    configured: bool
    reachable: bool
    lapi_url: str
    detail: str | None = None


__all__ = [
    "Alert",
    "AlertList",
    "AlertSource",
    "CrowdSecHealth",
    "Decision",
    "DecisionCreate",
    "DecisionList",
]
