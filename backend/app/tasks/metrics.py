"""Scrape this node's nginx and store the sample.

Runs on every node's beat. In HA the task is routed to the node's OWN queue
(see ``_configure_ha``): a tick executed on another node would scrape *that*
node's nginx and upsert *its* row, leaving this node unmeasured and the other
counted twice — silently, since both writes look perfectly valid.

A failed scrape writes nothing. The previous row then ages out of the totals on
the staleness rule, which is the honest outcome: unknown, not zero.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings
from app.services.dashboard.metrics import record_sample
from app.services.nginx.stub_status import ParseError, parse_stub_status

log = logging.getLogger(__name__)

# Whether a scrape failure has already been reported at warning level.
_warned = False


async def _fetch(url: str) -> str:
    """GET the status body. Short timeout: a slow answer is worthless when the
    next scrape is seconds away."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


async def _scrape_async(*, session_factory=None) -> None:
    global _warned

    try:
        body = await _fetch(settings.nginx_status_url)
        sample = parse_stub_status(body)
    except (OSError, ParseError, httpx.HTTPError) as exc:
        # The FIRST failure is a warning; the rest are debug.
        #
        # A node whose nginx blips would otherwise fill the log every interval
        # with something the dashboard already shows. But a scrape that has
        # NEVER succeeded means the traffic card will sit empty forever, and an
        # operator has nothing to go on — which is exactly what happened the
        # first time this shipped.
        if not _warned:
            _warned = True
            log.warning(
                "stub_status scrape failed at %s: %s. The Live traffic card "
                "stays empty until this succeeds. Check that nginx exposes "
                ":8081 (rebuild the nginx image if it predates that change).",
                settings.nginx_status_url,
                exc,
            )
        else:
            log.debug("stub_status scrape failed: %s", exc)
        return

    if _warned:
        # Say so once, so a log that reported a problem also reports its end.
        _warned = False
        log.warning("stub_status scrape recovered at %s", settings.nginx_status_url)

    if session_factory is None:
        engine = create_async_engine(settings.database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        await record_sample(session, settings.effective_node_id, sample, now=datetime.now(UTC))


@celery_app.task(name="app.tasks.metrics.scrape_local_nginx")
def scrape_local_nginx() -> None:
    """Celery entrypoint. Celery runs outside FastAPI's session scope, so the
    async body opens its own engine — the pattern ``app/tasks/certs.py`` uses."""
    asyncio.run(_scrape_async())
