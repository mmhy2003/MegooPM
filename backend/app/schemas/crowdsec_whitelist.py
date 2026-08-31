"""Pydantic schemas for UI-authored CrowdSec whitelists.

Validation mirrors the database ``CHECK`` constraint and the renderer's own
checks, so bad input is a 422 at the API boundary rather than a 500 from the
database or — far worse — a parser file CrowdSec refuses to load, which stops
it starting and leaves the bouncer failing closed.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.crowdsec.whitelists import WhitelistValidationError, validate_entries


class WhitelistBase(BaseModel):
    """Fields describing one whitelist document."""

    name: str = Field(min_length=1, max_length=255, description="Operator-facing name")
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
    enabled: bool = Field(
        default=True, description="Disabled whitelists are not rendered"
    )

    @model_validator(mode="after")
    def _check_entries(self) -> WhitelistBase:
        if not self.ips and not self.cidrs:
            raise ValueError("A whitelist needs at least one IP address or CIDR range.")
        try:
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
    description: str
    ips: list[str]
    cidrs: list[str]
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
