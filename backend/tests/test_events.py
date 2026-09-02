"""The event channel.

The framing tests matter more than they look: a malformed SSE frame errors
nowhere — the browser simply never fires an event — so the feature appears to
work while doing nothing.

The round-trip test needs a real Redis, and skips without one.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest
from app.schemas.events import Event
from app.services.events import format_sse
from redis.exceptions import RedisError

pytestmark = pytest.mark.asyncio

AT = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


# --- Framing (pure) --------------------------------------------------------


def test_a_frame_ends_with_a_blank_line() -> None:
    """Without the terminator a browser buffers forever and fires nothing."""
    frame = format_sse(Event(type="config.changed", at=AT, detail={}))
    assert frame.endswith("\n\n")


def test_a_frame_carries_one_data_line_of_json() -> None:
    frame = format_sse(Event(type="config.changed", at=AT, detail={"version": 7}))
    lines = [line for line in frame.split("\n") if line]
    assert len(lines) == 1
    assert lines[0].startswith("data: ")
    payload = json.loads(lines[0][len("data: ") :])
    assert payload["type"] == "config.changed"
    assert payload["detail"]["version"] == 7


def test_the_payload_never_contains_a_raw_newline() -> None:
    """A newline inside the JSON would split one frame into two malformed ones,
    and the browser would silently drop both."""
    frame = format_sse(Event(type="certificate.changed", at=AT, detail={"n": "a\nb"}))
    assert frame.count("\n\n") == 1
    assert len([line for line in frame.split("\n") if line]) == 1


def test_an_empty_detail_still_produces_a_valid_frame() -> None:
    frame = format_sse(Event(type="stream.open", at=AT))
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")


# --- Publish / subscribe (needs Redis) -------------------------------------


async def test_a_published_event_reaches_a_subscriber() -> None:
    """The whole point: a publisher and a subscriber that never met, joined by
    Redis — which is what makes this work across nodes."""
    from app.services.events import publish, subscribe

    received: list[Event] = []

    async def listen() -> None:
        async for event in subscribe():
            received.append(event)
            return

    task = asyncio.create_task(listen())
    await asyncio.sleep(0.3)  # let the subscription register before publishing
    try:
        await publish(Event(type="config.changed", at=AT, detail={"version": 3}))
        await asyncio.wait_for(task, timeout=5)
    except (TimeoutError, OSError, RedisError):  # pragma: no cover - no Redis available
        task.cancel()
        pytest.skip("No Redis reachable at REDIS_URL")

    assert received[0].type == "config.changed"
    assert received[0].detail["version"] == 3


async def test_a_publish_failure_does_not_raise(monkeypatch) -> None:
    """A certificate that renewed but could not announce itself has still
    renewed; announcing must never fail the operation that raised it."""
    from app.services import events

    class Broken:
        async def publish(self, *args, **kwargs):
            raise OSError("redis is gone")

        async def aclose(self):
            return None

    monkeypatch.setattr(events, "_client", lambda: Broken())
    await events.publish(Event(type="x", at=AT))  # must not raise
