"""Pydantic schemas for UI-authored CrowdSec whitelists.

Validation mirrors the database ``CHECK`` constraint and the renderer's own
checks, so bad input is a 422 at the API boundary rather than a 500 from the
database or — far worse — a parser file CrowdSec refuses to load, which stops
it starting and leaves the bouncer failing closed.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import WhitelistKind
from app.services.crowdsec.whitelists import (
    WhitelistValidationError,
    validate_entries,
    validate_expressions,
)


class WhitelistBase(BaseModel):
    """Fields describing one whitelist document."""

    name: str = Field(min_length=1, max_length=255, description="Operator-facing name")
    kind: WhitelistKind = Field(
        default=WhitelistKind.ip_cidr,
        description="What this whitelist matches on: addresses, or an expr expression",
    )
    reason: str = Field(
        min_length=1,
        description="Why these addresses are exempt; appears in CrowdSec's logs",
    )
    description: str = Field(default="", description="Free-text note")
    ips: list[str] = Field(
        default_factory=list, description="Exact IP addresses to exempt"
    )
    cidrs: list[str] = Field(
        default_factory=list, description="CIDR ranges to exempt"
    )
    filter: str | None = Field(
        default=None,
        description=(
            "Optional expr filter scoping which events the expressions are "
            "evaluated against (expression whitelists only)"
        ),
    )
    expressions: list[str] = Field(
        default_factory=list,
        description=(
            "CrowdSec expr expressions. Compiled by CrowdSec, not here — one "
            "that does not compile stops CrowdSec starting and is caught by the "
            "apply's rollback"
        ),
    )
    enabled: bool = Field(
        default=True, description="Disabled whitelists are not rendered"
    )

    @model_validator(mode="after")
    def _check_entries(self) -> WhitelistBase:
        """Validate per kind, and refuse the other kind's fields.

        Silently ignoring a stray `ips` on an expression whitelist would be
        worse than rejecting it: CrowdSec evaluates every key it finds, so a
        field the operator believes is inert would quietly widen the whitelist.
        """
        try:
            if self.kind is WhitelistKind.expression:
                if self.ips or self.cidrs:
                    raise ValueError(
                        "An expression whitelist cannot carry IPs or CIDR ranges; "
                        "use the IP / CIDR kind for those."
                    )
                validate_expressions(self.expressions)
            else:
                if self.expressions or self.filter:
                    raise ValueError(
                        "An IP / CIDR whitelist cannot carry expressions or a "
                        "filter; use the Expression kind for those."
                    )
                if not self.ips and not self.cidrs:
                    raise ValueError(
                        "A whitelist needs at least one IP address or CIDR range."
                    )
                validate_entries(self.ips, self.cidrs)
        except WhitelistValidationError as exc:
            raise ValueError(str(exc)) from exc
        return self


class WhitelistCreate(WhitelistBase):
    """Request body for creating a whitelist."""


class WhitelistUpdate(WhitelistBase):
    """Request body for replacing a whitelist."""


class WhitelistRead(WhitelistBase):
    """A stored whitelist.

    The inherited fields are redeclared without defaults so the generated
    OpenAPI marks them required. On a *response* they are always present, and
    leaving them optional pushes needless null-handling into every consumer.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: WhitelistKind
    description: str
    ips: list[str]
    cidrs: list[str]
    expressions: list[str]
    enabled: bool
    created_at: datetime
    updated_at: datetime


class WhitelistPreview(BaseModel):
    """The YAML a given whitelist would contribute to the parser file."""

    yaml: str = Field(description="Exactly what the renderer would write")


class WhitelistApplyStatus(BaseModel):
    """Whether the last render actually reached CrowdSec."""

    ok: bool = Field(description="False when the last apply failed")
    error: str | None = Field(
        default=None, description="Operator-facing failure text"
    )
    applied_at: datetime | None = Field(
        default=None, description="When the last apply attempt finished"
    )
    reload_configured: bool = Field(
        description=(
            "False when CROWDSEC_CONTROL_NODE_ID is unset; whitelists then save "
            "but are never applied"
        )
    )


__all__ = [
    "WhitelistApplyStatus",
    "WhitelistCreate",
    "WhitelistPreview",
    "WhitelistRead",
    "WhitelistUpdate",
]
