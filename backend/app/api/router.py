"""Top-level API router aggregation.

The versioned API (mounted under ``settings.api_v1_prefix``) is assembled here.
Feature tickets attach their routers to ``api_router``; the unversioned
``health`` route is wired directly in ``app.main``.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    access_lists,
    audit_log,
    auth,
    certificates,
    cluster,
    crowdsec,
    custom_pages,
    dead_hosts,
    dns_providers,
    nginx,
    proxy_hosts,
    redirection_hosts,
    streams,
    tasks,
    upstreams,
    users,
)

api_router = APIRouter()

api_router.include_router(tasks.router)
api_router.include_router(auth.router, prefix="/auth")
api_router.include_router(users.router, prefix="/users")
api_router.include_router(audit_log.router, prefix="/audit-log")
api_router.include_router(nginx.router, prefix="/nginx")
api_router.include_router(cluster.router, prefix="/cluster")
api_router.include_router(certificates.router, prefix="/certificates")
# Paths already carry /dns-providers and /dns-credentials (two resources, one module).
api_router.include_router(dns_providers.router)
api_router.include_router(crowdsec.router, prefix="/crowdsec")
api_router.include_router(upstreams.router, prefix="/upstreams")
api_router.include_router(access_lists.router, prefix="/access-lists")
api_router.include_router(proxy_hosts.router, prefix="/proxy-hosts")
api_router.include_router(redirection_hosts.router, prefix="/redirection-hosts")
api_router.include_router(dead_hosts.router, prefix="/dead-hosts")
api_router.include_router(streams.router, prefix="/streams")
api_router.include_router(custom_pages.router, prefix="/custom-pages")

# Additional feature routers are included here as they are added, for example:
# from app.api.routes import projects
# api_router.include_router(projects.router, prefix="/projects")
