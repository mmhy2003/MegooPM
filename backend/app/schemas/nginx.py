"""Schemas for the nginx config/reload endpoints."""

from __future__ import annotations

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


__all__ = ["NginxConfigFile", "NginxConfigPreview"]
