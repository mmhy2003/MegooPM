"""Publishing and relaying dashboard events over Redis.

Redis rather than an in-process bus because MegooPM runs multi-node: an event
raised by a Celery task on node B must reach a browser connected to node A. HA
already mandates a shared Redis, so this costs nothing new.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import redis.asyncio as aioredis

from app.core.config import settings
from app.schemas.events import Event

log = logging.getLogger(__name__)

CHANNEL = "megoopm:events"


def format_sse(event: Event) -> str:
    """One SSE frame.

    ``model_dump_json`` never emits a raw newline, so the payload cannot split
    the frame. The trailing blank line is the terminator: without it a browser
    buffers indefinitely and fires nothing, with no error anywhere.
    """
    return f"data: {event.model_dump_json()}\n\n"


def _client() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def publish(event: Event) -> None:
    """Announce an event. Never raises.

    Announcing is never worth failing the operation that raised it: a
    certificate that renewed but could not announce itself has still renewed.
    """
    client = _client()
    try:
        await client.publish(CHANNEL, event.model_dump_json())
    except Exception as exc:  # noqa: BLE001 - announcing must not break the caller
        log.debug("event publish failed: %s", exc)
    finally:
        await client.aclose()


async def subscribe() -> AsyncIterator[Event]:
    """Yield events until the caller stops consuming."""
    client = _client()
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(CHANNEL)
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                yield Event.model_validate_json(message["data"])
            except ValueError:
                # Someone else publishing on this channel, or a version skew.
                # Skipping one message beats ending every client's stream.
                continue
    finally:
        await pubsub.aclose()
        await client.aclose()


__all__ = ["CHANNEL", "format_sse", "publish", "subscribe"]
