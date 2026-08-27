"""FastAPI application entrypoint.

Builds the ASGI app, wires CORS, and mounts routers. Run locally with:

    uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.api.routes import health
from app.core.config import settings
from app.db.session import SessionLocal, engine

logger = logging.getLogger(__name__)


async def _bootstrap_crowdsec() -> None:
    """Best-effort CrowdSec auto-registration on startup (MEG-43).

    Ensures DB-backed LAPI credentials exist (self-registering a machine, or
    seeding from env) so a fresh stack needs zero manual ``cscli``/env steps.
    Fully swallowed on failure — a cold start where CrowdSec or the DB isn't
    ready yet must never block the API from coming up; the request path retries
    the env seed lazily, and the next startup retries registration.
    """
    from app.services.crowdsec import registration

    try:
        async with SessionLocal() as session:
            await registration.ensure_registered(session)
    except Exception as exc:  # noqa: BLE001 - startup must not crash on this
        logger.warning("CrowdSec auto-registration skipped: %s", exc)


async def _bootstrap_first_admin() -> None:
    """Best-effort initial-admin seed on startup.

    Creates ``FIRST_ADMIN_EMAIL`` / ``FIRST_ADMIN_PASSWORD`` as an admin only
    while the users table is empty (see ``user_service.ensure_first_admin``),
    so a fresh stack has a login without a manual ``scripts.create_user`` step.
    Swallowed on failure: a DB that isn't reachable yet, or a sibling node that
    won the seed race, must never keep the API from coming up. The CLI remains
    the manual fallback.
    """
    from app.services import user as user_service

    try:
        async with SessionLocal() as session:
            await user_service.ensure_first_admin(
                session,
                email=settings.first_admin_email,
                password=settings.first_admin_password,
            )
    except Exception as exc:  # noqa: BLE001 - startup must not crash on this
        logger.warning("Initial admin seed skipped: %s", exc)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: seed the first admin, bootstrap CrowdSec, dispose
    the DB engine on exit."""
    await _bootstrap_first_admin()
    await _bootstrap_crowdsec()
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    """Application factory."""
    app = FastAPI(
        title=settings.project_name,
        debug=settings.debug,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Unversioned liveness endpoint (load balancers / orchestrators).
    app.include_router(health.router)

    # Versioned application API.
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
