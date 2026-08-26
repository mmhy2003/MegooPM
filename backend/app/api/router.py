"""Top-level API router aggregation.

The versioned API (mounted under ``settings.api_v1_prefix``) is assembled here.
Feature tickets attach their routers to ``api_router``; the unversioned
``health`` route is wired directly in ``app.main``.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import audit_log, auth, nginx, tasks, users

api_router = APIRouter()

api_router.include_router(tasks.router)
api_router.include_router(auth.router, prefix="/auth")
api_router.include_router(users.router, prefix="/users")
api_router.include_router(audit_log.router, prefix="/audit-log")
api_router.include_router(nginx.router, prefix="/nginx")

# Additional feature routers are included here as they are added, for example:
# from app.api.routes import projects
# api_router.include_router(projects.router, prefix="/projects")
