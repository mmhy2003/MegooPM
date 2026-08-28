"""CrowdSec integration service (MEG-22, MEG-43).

Public facade over the LAPI client and its dependency factory. The dependency
now resolves credentials from the database (self-registered / env-seeded, see
:mod:`app.services.crowdsec.credentials`) rather than reading ``settings``
directly, while the client itself stays ``Settings``-driven via an overlay.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.crowdsec import credentials
from app.services.crowdsec.client import (
    CrowdSecClient,
    CrowdSecError,
    CrowdSecNotConfigured,
)


async def get_crowdsec_client(
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AsyncIterator[CrowdSecClient]:
    """FastAPI dependency: a request-scoped LAPI client built from DB creds.

    Credentials are resolved from the ``crowdsec_credentials`` table (seeded from
    env on first use if the DB is empty) and overlaid onto the base settings, so
    the client transparently uses DB-backed credentials. ``ensure_registered``
    runs first (cached, idempotent) so a machine missing at startup — CrowdSec
    not up yet, or only a bouncer key seeded — is registered on the next request.
    """
    from app.services.crowdsec import registration

    await registration.ensure_registered(db)
    settings = await credentials.resolve_settings(db)
    client = CrowdSecClient(settings)
    try:
        yield client
    finally:
        await client.aclose()


__all__ = [
    "CrowdSecClient",
    "CrowdSecError",
    "CrowdSecNotConfigured",
    "credentials",
    "get_crowdsec_client",
]
