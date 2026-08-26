"""FastAPI application entrypoint.

Builds the ASGI app, wires CORS, and mounts routers. Run locally with:

    uvicorn app.main:app --reload
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.api.routes import health
from app.core.config import settings
from app.db.session import engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: dispose of the DB engine on shutdown."""
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
