"""Shared test fixtures.

The smoke tests exercise the ASGI app in-process via httpx's ASGITransport, so
no running server or database is required for the health check.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """An httpx client bound to the ASGI app (no network, no DB)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
