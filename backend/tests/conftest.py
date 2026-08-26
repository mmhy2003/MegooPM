"""Shared test fixtures.

The smoke tests exercise the ASGI app in-process via httpx's ASGITransport, so
no running server or database is required for the health check.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

# Configure Celery for tests before the app (and thus the Celery app) is
# imported: run tasks inline and store their results in an in-process backend so
# they are retrievable via AsyncResult without a running Redis/worker.
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")
os.environ.setdefault("CELERY_RESULT_BACKEND", "cache+memory://")

import pytest
from app.main import app
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """An httpx client bound to the ASGI app (no network, no DB)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
