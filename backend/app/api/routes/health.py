"""Liveness endpoint.

``GET /health`` is intentionally dependency-free (no DB access) so it reports
process liveness for load balancers and container orchestrators. Readiness
checks that touch the database belong on a separate endpoint added later.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return service liveness."""
    return HealthResponse(
        status="ok",
        service=settings.project_name,
        environment=settings.environment,
    )
