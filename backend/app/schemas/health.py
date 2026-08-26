"""Schemas for the health/liveness endpoint."""

from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Liveness response body."""

    status: str = "ok"
    service: str
    environment: str
