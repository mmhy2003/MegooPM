"""Top-level API router aggregation.

The versioned API (mounted under ``settings.api_v1_prefix``) is assembled here.
Feature tickets attach their routers to ``api_router``; the unversioned
``health`` route is wired directly in ``app.main``.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import tasks

api_router = APIRouter()

api_router.include_router(tasks.router)

# Additional feature routers are included here as they are added, for example:
# from app.api.routes import projects
# api_router.include_router(projects.router, prefix="/projects")
