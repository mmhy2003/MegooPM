"""The dashboard event stream (admin-only).

Server-Sent Events, not WebSocket: the traffic is one-directional, so the
bidirectionality would buy nothing and cost a separate auth story and manual
reconnection.

Events are signals. A client that receives one refetches through the ordinary
REST path, so there is never a second serialisation of a domain object to drift
from the first.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api.deps import StreamAdminUser
from app.schemas.events import Event
from app.services.events import format_sse, subscribe

router = APIRouter(tags=["events"])

# Comfortably inside the 300s proxy_read_timeout an operator inherits if they
# front the admin API with a managed host, and frequent enough that a client
# can tell a live stream from a silently dead socket.
HEARTBEAT_SECONDS = 20


async def _pump(queue: asyncio.Queue[Event]) -> None:
    """Read the subscription and hand events to the stream.

    A separate task on purpose. Applying ``wait_for`` directly to
    ``subscribe().__anext__()`` would cancel an in-flight step of the async
    generator on every heartbeat, which can leave it in a broken state —
    whereas cancelling ``queue.get()`` is safe.
    """
    async for event in subscribe():
        await queue.put(event)


async def _stream(request: Request) -> AsyncIterator[str]:
    # Subscribe BEFORE the first yield. An async generator suspends at a yield,
    # so creating the pump afterwards would leave the subscription dormant
    # until the client consumed that frame — and any event published in the
    # gap would be missed. The window is small with a real browser and is not
    # zero, which is the kind of race that shows up once a month and is never
    # reproduced.
    queue: asyncio.Queue[Event] = asyncio.Queue()
    pump = asyncio.create_task(_pump(queue))
    try:
        # An immediate frame: it flushes any intermediary's buffer and tells
        # the client the stream is live rather than merely accepted.
        yield format_sse(Event(type="stream.open", at=datetime.now(UTC)))

        while True:
            if await request.is_disconnected():
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                # A comment frame. Browsers ignore it; proxies see traffic.
                yield ": keepalive\n\n"
                continue
            yield format_sse(event)
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pump


@router.get("")
async def stream_events(_admin: StreamAdminUser, request: Request) -> StreamingResponse:
    """Relay dashboard events until the client disconnects."""
    return StreamingResponse(
        _stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Tells nginx not to buffer, for anyone proxying the admin API.
            # Harmless everywhere else.
            "X-Accel-Buffering": "no",
        },
    )
