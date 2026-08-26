"""CrowdSec integration service (MEG-22).

Public facade over the LAPI client and its dependency factory.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.services.crowdsec.client import (
    CrowdSecClient,
    CrowdSecError,
    CrowdSecNotConfigured,
)


async def get_crowdsec_client() -> AsyncIterator[CrowdSecClient]:
    """FastAPI dependency: a request-scoped LAPI client, closed on teardown."""
    client = CrowdSecClient()
    try:
        yield client
    finally:
        await client.aclose()


__all__ = [
    "CrowdSecClient",
    "CrowdSecError",
    "CrowdSecNotConfigured",
    "get_crowdsec_client",
]
