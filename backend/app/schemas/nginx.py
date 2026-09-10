"""Schemas for the nginx config/reload endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class NginxConfigFile(BaseModel):
    """One rendered managed ``.conf`` file."""

    name: str
    content: str


class NginxConfigPreview(BaseModel):
    """The full config the engine *would* write for the current DB state.

    A read-only render — it does not touch disk or reload nginx, so the
    frontend can show operators the generated output before/without applying.
    """

    files: list[NginxConfigFile] = Field(default_factory=list)


class NginxApplyStatus(BaseModel):
    """How the most recent apply went.

    ``ok`` is None until an apply has been recorded — a fresh install, or one
    upgraded before its first apply. The UI shows nothing for that rather than
    claiming a failure it has no evidence of.
    """

    ok: bool | None = None
    at: datetime | None = None
    message: str | None = None
    #: nginx's own words when the apply failed; empty on success.
    output: str | None = None


__all__ = ["NginxApplyStatus", "NginxConfigFile", "NginxConfigPreview"]
