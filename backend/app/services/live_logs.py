"""Live logs: the pieces the WebSocket is built from.

nginx on every node publishes the lines somebody is watching to
``megoopm:live:<host_key>`` (see infra/nginx/lua/megoopm_live_relay.lua); the
socket subscribes to the channels its page asks for and forwards each message.
Nothing is stored anywhere: Redis is used for pub/sub only, and a line that
nobody is subscribed to at the moment it is published is gone.

Everything with a clock, a connection or a database is reached through
:class:`LiveRuntime`, so the socket can be tested with a fake clock and a fake
pub/sub and no Redis at all.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.user import User
from app.schemas.crowdsec import Decision
from app.services import user as user_service
from app.services.crowdsec import get_crowdsec_client
from app.services.crowdsec.geo import country_for

log = logging.getLogger(__name__)

CHANNEL_PREFIX = "megoopm:live:"

# p<id> proxy, r<id> redirection, d<id> dead, s<id> stream, u the default
# servers, n nginx itself (its own messages, no host), and all: every host's
# lines plus nginx's own, which the relay also publishes on one channel.
# Anchored with \Z, not $: `$` also matches before a trailing newline.
_HOST_KEY = re.compile(r"(?:[prds][1-9]\d*|u|n|all)\Z")


def parse_host_key(key: str) -> str:
    """``key`` if it names a host, else ``ValueError``.

    The key becomes part of a Redis channel name, and it comes from the
    browser: this is the boundary that keeps it from naming anything else.
    """
    if not isinstance(key, str) or not _HOST_KEY.match(key):
        raise ValueError(f"not a host key: {key!r}")
    return key


def channel_for(key: str) -> str:
    """The pub/sub channel the relay publishes ``key``'s lines on."""
    return CHANNEL_PREFIX + parse_host_key(key)


class RateLimiter:
    """At most ``per_second`` lines a second; counts what it turns away.

    Skipped lines are reported, never silent, but a burst is reported as one
    count rather than a frame per line: :meth:`take_skipped` hands the count
    over once the second it was skipped in is over, and at most once a second.
    """

    def __init__(self, per_second: int = 200) -> None:
        self._per_second = per_second
        self._window_start = 0.0
        self._in_window = 0
        self._skipped = 0
        self._report_at: float | None = None
        self._last_report: float | None = None

    def allow(self, now: float) -> bool:
        if self._in_window == 0 or now - self._window_start >= 1.0:
            self._window_start = now
            self._in_window = 0
        if self._in_window < self._per_second:
            self._in_window += 1
            return True
        if self._report_at is None:
            due = self._window_start + 1.0
            if self._last_report is not None:
                due = max(due, self._last_report + 1.0)
            self._report_at = due
        self._skipped += 1
        return False

    def add_skipped(self, count: int, now: float) -> None:
        """Count lines skipped before they reached this session (the relay's cap).

        Reported with the session's own, so the page's one "N lines skipped"
        notice stays the whole truth: as soon as a tick comes, and still at
        most once a second.
        """
        if count <= 0:
            return
        self._skipped += count
        if self._report_at is None:
            due = now
            if self._last_report is not None:
                due = max(due, self._last_report + 1.0)
            self._report_at = due

    def take_skipped(self, now: float) -> int:
        if self._report_at is None or now < self._report_at:
            return 0
        count, self._skipped = self._skipped, 0
        self._report_at = None
        self._last_report = now
        return count


def new_decisions(seen: set[int], decisions: Sequence[Decision]) -> list[Decision]:
    """The decisions whose ids are not in ``seen``, which now holds them too.

    A decision without an id is never reported: it could not be told apart
    from itself on the next read.
    """
    fresh: list[Decision] = []
    for decision in decisions:
        if decision.id is None or decision.id in seen:
            continue
        seen.add(decision.id)
        fresh.append(decision)
    return fresh


def _reject_constant(name: str) -> Any:
    # NaN and Infinity parse in Python but are not JSON, and a browser's
    # JSON.parse refuses the frame that carries one.
    raise ValueError(f"non-JSON constant {name}")


def line_frame(data: Any) -> dict[str, Any] | None:
    """A published message as a ``line`` frame, or ``None`` to drop it.

    Lenient where it costs nothing and strict where it would break the page:
    invalid UTF-8 (a hostile user agent can carry it through the relay) is
    replaced, while anything that is not a JSON object is dropped — someone
    else publishing on the channel, or a version skew, must never end the
    session.
    """
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    if not isinstance(data, str):
        return None
    try:
        message = json.loads(data, parse_constant=_reject_constant)
    except ValueError:
        return None
    if not isinstance(message, dict):
        return None
    fields = message.get("f")
    ip = fields.get("ip") if isinstance(fields, dict) else None
    cc = country_for("Ip", ip) if isinstance(ip, str) and ip else None
    return {**message, "type": "line", "cc": cc}


# The relay's own message when it drops a host's lines over its per-node cap:
# {"log":"skipped","node":…,"host":…,"count":N}, written with "log" first so it
# is told from a line by this prefix, before anything is decoded.
RELAY_SKIPPED_PREFIX = b'{"log":"skipped",'
# Far above what a relay can skip in a second; a larger number is not a count.
_MAX_RELAY_SKIPPED = 1_000_000


def relay_skipped(data: Any) -> int | None:
    """The count in the relay's skipped message, or ``None`` if ``data`` is not one.

    A message in that form whose count is not a sane whole number counts 0:
    it is still never shown as a line.
    """
    if isinstance(data, str):
        data = data.encode("utf-8", errors="replace")
    if not isinstance(data, bytes) or not data.startswith(RELAY_SKIPPED_PREFIX):
        return None
    try:
        message = json.loads(data, parse_constant=_reject_constant)
    except ValueError:
        return 0
    count = message.get("count") if isinstance(message, dict) else None
    if not isinstance(count, int) or isinstance(count, bool):
        return 0
    return count if 0 < count <= _MAX_RELAY_SKIPPED else 0


def decision_frame(decision: Decision) -> dict[str, Any]:
    """A new CrowdSec decision, in the shape the page reads."""
    return {
        "type": "decision",
        "value": decision.value,
        "scope": decision.scope,
        "type_": decision.type,
        "scenario": decision.scenario,
        "duration": decision.duration,
        "origin": decision.origin,
        "cc": decision.country or country_for(decision.scope, decision.value),
    }


def dump_frame(frame: dict[str, Any]) -> str:
    """Serialise a frame for the socket.

    ASCII-escaped on purpose: ``"\\ud800"`` is valid JSON that decodes to a lone
    surrogate, which cannot be encoded as UTF-8. Escaped, it travels as six
    ASCII characters and the browser decodes it; unescaped, sending it would
    raise and end the session.
    """
    return json.dumps(frame, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def session_still_valid(user: User | None, token_version: int) -> bool:
    """Whether ``user`` may still watch: active, admin, and not signed out since."""
    return (
        user is not None
        and user.is_active
        and user.is_admin
        and user.token_version == token_version
    )


async def load_user(factory: async_sessionmaker[AsyncSession], user_id: int) -> User | None:
    """Read the user in a session of its own, closed at once.

    A WebSocket lives for hours; a session held for its lifetime would hold a
    pooled connection, inside a transaction, for just as long.
    """
    async with factory() as db:
        return await user_service.get_by_id(db, user_id)


class PubSub(Protocol):
    async def subscribe(self, *channels: str) -> None: ...

    async def unsubscribe(self, *channels: str) -> None: ...

    async def get_message(self, *, timeout: float) -> dict[str, Any] | None: ...

    async def aclose(self) -> None: ...


class _RedisPubSub:
    """One Redis connection's subscription, closed with its client."""

    def __init__(self) -> None:
        # Bytes, not decode_responses: a line can carry invalid UTF-8, which
        # redis-py's decoder would raise on. line_frame decodes leniently.
        self._client = aioredis.from_url(settings.redis_url)
        self._pubsub = self._client.pubsub()

    async def subscribe(self, *channels: str) -> None:
        await self._pubsub.subscribe(*channels)

    async def unsubscribe(self, *channels: str) -> None:
        await self._pubsub.unsubscribe(*channels)

    async def get_message(self, *, timeout: float) -> dict[str, Any] | None:
        return await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=timeout)

    async def aclose(self) -> None:
        try:
            await self._pubsub.aclose()
        finally:
            await self._client.aclose()


def open_pubsub() -> PubSub:
    return _RedisPubSub()


async def list_decisions() -> list[Decision]:
    """CrowdSec's active decisions — the same read the Security page does."""
    async with (
        SessionLocal() as db,
        asynccontextmanager(get_crowdsec_client)(db) as client,
    ):
        return await client.list_decisions()


@dataclass(frozen=True)
class LiveRuntime:
    """What the socket needs from the outside world, so tests can replace it."""

    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    open_pubsub: Callable[[], PubSub] = open_pubsub
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal
    list_decisions: Callable[[], Awaitable[Sequence[Decision]]] = list_decisions


_RUNTIME = LiveRuntime()


def get_live_runtime() -> LiveRuntime:
    """FastAPI dependency: the real clock, Redis, database and CrowdSec."""
    return _RUNTIME


__all__ = [
    "CHANNEL_PREFIX",
    "LiveRuntime",
    "PubSub",
    "RateLimiter",
    "channel_for",
    "decision_frame",
    "dump_frame",
    "get_live_runtime",
    "line_frame",
    "load_user",
    "new_decisions",
    "open_pubsub",
    "parse_host_key",
    "session_still_valid",
]
