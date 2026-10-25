"""Live logs (admin, signed in to a browser): a ticket, and the socket it opens.

A WebSocket, unlike the SSE stream in events.py, because the page talks back:
it says which hosts it watches, and changes that without reconnecting.

**Auth.** A browser cannot set headers on a WebSocket, and the session cookie is
host-only (events.py tells that story), so the socket cannot authenticate the
way every other route does. Instead the page first calls ``POST /ticket`` —
an ordinary bearer-authenticated route, guarded by ``SessionAdminUser`` so an
API key is refused — and passes the 30-second ticket it gets as
``?ticket=``. The ticket is a signed JWT of type ``live`` carrying the user's
``token_version``; nothing is stored. The socket re-reads the user when it opens
and every 60 s after, and closes with 4403 if they were disabled, demoted, or
signed out everywhere since.

**Close codes.** 4401: the ticket is missing, bad, expired, or names a user who
may not watch — get a new ticket. 4403: the session stopped qualifying while
open — do not retry with the same session. 4400: a frame the server does not
understand. 1011: the re-check could not reach the database.

**Frames.** Client → server, at any time:
``{"type": "watch", "hosts": ["p1", "u", ...], "decisions": bool}``.
Server → client: ``{"type": "line", ...the published message, "cc"}``,
``{"type": "decision", ...}`` (see ``live_logs.decision_frame``),
``{"type": "skipped", "count": N}`` and ``{"type": "ping"}`` every 25 s. The
skipped count includes the lines a relay dropped over its own per-host cap,
which it reports on the host's channel (``live_logs.relay_skipped``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketException

from app.api.deps import SessionAdminUser
from app.core.security import LIVE_TICKET_SECONDS, create_live_ticket, decode_token
from app.schemas.live_logs import LiveTicket
from app.services.live_logs import (
    LiveRuntime,
    PubSub,
    RateLimiter,
    channel_for,
    decision_frame,
    dump_frame,
    get_live_runtime,
    line_frame,
    load_user,
    new_decisions,
    parse_host_key,
    relay_skipped,
    session_still_valid,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["live-logs"])

PING_SECONDS = 25.0
RECHECK_SECONDS = 60.0
DECISIONS_SECONDS = 5.0
# How often the timers above are looked at. Every interval is a whole number
# of seconds, so a one-second tick is late by under a second at worst.
TICK_SECONDS = 1.0
# The longest a watch change waits for a Redis read already in progress.
READ_TIMEOUT_SECONDS = 1.0
REDIS_RETRY_SECONDS = 5.0
RECHECK_TIMEOUT_SECONDS = 10.0
# A page watches what an admin picked; this only stops a frame from asking
# Redis for an unbounded SUBSCRIBE.
MAX_WATCH_HOSTS = 1000

CLOSE_BAD_FRAME = 4400
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_INTERNAL = 1011

Runtime = Annotated[LiveRuntime, Depends(get_live_runtime)]


@router.post("/ticket", response_model=LiveTicket)
async def issue_live_ticket(admin: SessionAdminUser) -> LiveTicket:
    """A 30-second ticket that opens ``/live-logs/ws``. Browser sessions only."""
    return LiveTicket(
        ticket=create_live_ticket(admin.id, token_version=admin.token_version),
        expires_in=LIVE_TICKET_SECONDS,
    )


@dataclass(frozen=True)
class LiveViewer:
    user_id: int
    token_version: int


async def _viewer_from_ticket(runtime: LiveRuntime, ticket: str | None) -> LiveViewer | None:
    if not ticket:
        return None
    try:
        payload = decode_token(ticket, expected_type="live")
        user_id = int(payload["sub"])
        token_version = payload["tv"]
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        return None
    if not isinstance(token_version, int) or isinstance(token_version, bool):
        return None
    user = await load_user(runtime.session_factory, user_id)
    if not session_still_valid(user, token_version):
        return None
    return LiveViewer(user_id=user_id, token_version=token_version)


async def require_live_ticket(
    websocket: WebSocket,
    runtime: Runtime,
    ticket: Annotated[str | None, Query()] = None,
) -> LiveViewer:
    """The socket's guard: a valid ticket for a user who may watch, or 4401.

    Named here and classified "session_admin" by tests/test_route_authorization.py:
    only ``POST /ticket``, behind ``SessionAdminUser``, issues the ticket, and
    this re-checks the role and ``token_version`` it was issued for.
    """
    viewer = await _viewer_from_ticket(runtime, ticket)
    if viewer is None:
        # Accept, then close. A socket refused during the handshake reaches a
        # browser as code 1006 with no reason, so the page could not tell "get
        # a new ticket" from "the network is down". Nothing is read from the
        # client and nothing but the close is sent.
        await websocket.accept()
        raise WebSocketException(code=CLOSE_UNAUTHORIZED)
    return viewer


def _parse_watch(text: str | None) -> tuple[frozenset[str], bool] | None:
    """``(host keys, decisions)`` from a watch frame, or ``None`` if it is not one."""
    if text is None:
        return None
    try:
        frame = json.loads(text)
    except ValueError:
        return None
    if not isinstance(frame, dict) or frame.get("type") != "watch":
        return None
    hosts = frame.get("hosts")
    decisions = frame.get("decisions", False)
    if not isinstance(hosts, list) or len(hosts) > MAX_WATCH_HOSTS:
        return None
    if not isinstance(decisions, bool):
        return None
    try:
        return frozenset(parse_host_key(host) for host in hosts), decisions
    except ValueError:
        return None


class _Session:
    """One open socket: four tasks, and the first to finish ends it.

    - ``_receive`` applies watch frames, and ends on disconnect or a bad frame;
    - ``_pump`` keeps the subscription matching the watch and forwards lines;
    - ``_tick`` pings, reports skipped lines, and re-checks the user;
    - ``_poll_decisions`` reads CrowdSec while decisions are watched.
    """

    def __init__(self, websocket: WebSocket, viewer: LiveViewer, runtime: LiveRuntime) -> None:
        self._ws = websocket
        self._viewer = viewer
        self._rt = runtime
        self._limiter = RateLimiter()
        self._send_lock = asyncio.Lock()
        self._channels: frozenset[str] = frozenset()
        self._decisions = False
        self._watch_changed = asyncio.Event()
        # Read before the socket is accepted, so every timer counts from a
        # moment the client cannot have seen past.
        self._started = runtime.clock()

    async def run(self) -> None:
        tasks = [
            asyncio.create_task(self._receive()),
            asyncio.create_task(self._pump()),
            asyncio.create_task(self._tick()),
            asyncio.create_task(self._poll_decisions()),
        ]
        code: int | None = None
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            errors = [task.exception() for task in done]
            if any(errors):
                # Most often a send to a client that has just gone.
                log.debug("live logs session ended: %r", errors)
                code = CLOSE_INTERNAL
            else:
                code = next(iter(done)).result()
        finally:
            for task in tasks:
                task.cancel()
            # wait, not gather: if this task is itself cancelled meanwhile (a
            # server shutdown), gather re-raises a child's CancelledError in
            # place of ours, and the caller's cancel scope does not recognise
            # it as its own.
            await asyncio.wait(tasks)
            for task in tasks:
                if not task.cancelled():
                    task.exception()  # retrieved: never "never retrieved"
        if code is not None:
            # The client may already be gone; there is nobody to tell.
            with contextlib.suppress(Exception):
                await self._ws.close(code=code)

    async def _send(self, frame: dict[str, Any]) -> None:
        text = dump_frame(frame)
        async with self._send_lock:
            await self._ws.send_text(text)

    async def _receive(self) -> int | None:
        while True:
            message = await self._ws.receive()
            if message["type"] == "websocket.disconnect":
                return None
            watch = _parse_watch(message.get("text"))
            if watch is None:
                return CLOSE_BAD_FRAME
            hosts, self._decisions = watch
            self._channels = frozenset(channel_for(host) for host in hosts)
            self._watch_changed.set()

    async def _pump(self) -> None:
        pubsub: PubSub | None = None
        current: set[str] = set()
        try:
            while True:
                wanted = self._channels
                if not wanted and not current:
                    self._watch_changed.clear()
                    if not self._channels:
                        await self._watch_changed.wait()
                    continue
                try:
                    if pubsub is None:
                        pubsub = self._rt.open_pubsub()
                    if wanted != current:
                        if gone := current - wanted:
                            await pubsub.unsubscribe(*gone)
                            current -= gone
                        if added := wanted - current:
                            await pubsub.subscribe(*added)
                            current |= added
                        continue
                    message = await pubsub.get_message(timeout=READ_TIMEOUT_SECONDS)
                except Exception as exc:  # noqa: BLE001 - Redis down must not end the socket
                    log.debug("live logs: Redis unavailable, retrying: %s", exc)
                    if pubsub is not None:
                        with contextlib.suppress(Exception):
                            await pubsub.aclose()
                    pubsub, current = None, set()
                    await self._rt.sleep(REDIS_RETRY_SECONDS)
                    continue
                if message is not None:
                    await self._forward(message)
        finally:
            if pubsub is not None:
                with contextlib.suppress(Exception):
                    await pubsub.aclose()

    async def _forward(self, message: dict[str, Any]) -> None:
        data = message.get("data")
        # The relay's count of lines it dropped over its own cap: a count, not
        # a line, so it is added to this session's and never capped itself.
        # Told apart by a prefix, so a line still costs no parse here.
        relayed_skips = relay_skipped(data)
        if relayed_skips is not None:
            self._limiter.add_skipped(relayed_skips, self._rt.clock())
            return
        # The cap first: under a flood, a skipped line costs no parse and no
        # country lookup.
        if not self._limiter.allow(self._rt.clock()):
            return
        frame = line_frame(data)
        if frame is not None:
            await self._send(frame)

    async def _tick(self) -> int:
        next_ping = self._started + PING_SECONDS
        next_check = self._started + RECHECK_SECONDS
        while True:
            await self._rt.sleep(TICK_SECONDS)
            now = self._rt.clock()
            if now >= next_check:
                next_check = now + RECHECK_SECONDS
                code = await self._recheck()
                if code is not None:
                    return code
            if now >= next_ping:
                next_ping = now + PING_SECONDS
                await self._send({"type": "ping"})
            if skipped := self._limiter.take_skipped(now):
                await self._send({"type": "skipped", "count": skipped})

    async def _recheck(self) -> int | None:
        try:
            user = await asyncio.wait_for(
                load_user(self._rt.session_factory, self._viewer.user_id),
                timeout=RECHECK_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - unconfirmed is not allowed
            log.warning("live logs: could not re-check the session: %s", exc)
            return CLOSE_INTERNAL
        if session_still_valid(user, self._viewer.token_version):
            return None
        return CLOSE_FORBIDDEN

    async def _poll_decisions(self) -> None:
        seen: set[int] = set()
        primed = False
        next_poll = float("-inf")
        while True:
            if not self._decisions:
                seen.clear()
                primed = False
                next_poll = float("-inf")
            elif (now := self._rt.clock()) >= next_poll:
                next_poll = now + DECISIONS_SECONDS
                # The first read only learns what exists already: a page
                # opened on a server holding thousands of community bans wants
                # the new ones, not a frame for each of those.
                if await self._read_decisions(seen, announce=primed):
                    primed = True
            await self._rt.sleep(TICK_SECONDS)

    async def _read_decisions(self, seen: set[int], *, announce: bool) -> bool:
        """Note the current decisions, sending the new ones if ``announce``."""
        try:
            current = await self._rt.list_decisions()
        except Exception as exc:  # noqa: BLE001 - CrowdSec down or not set up
            log.debug("live logs: could not read decisions: %s", exc)
            return False
        fresh = new_decisions(seen, current)
        if announce:
            for decision in fresh:
                if self._limiter.allow(self._rt.clock()):
                    await self._send(decision_frame(decision))
        # Forget the ones that lifted, so a session open for days does not
        # keep every id it ever saw.
        seen.intersection_update(d.id for d in current if d.id is not None)
        return True


@router.websocket("/ws")
async def live_logs_socket(
    websocket: WebSocket,
    viewer: Annotated[LiveViewer, Depends(require_live_ticket)],
    runtime: Runtime,
) -> None:
    """Stream the watched hosts' lines until the client leaves or stops qualifying."""
    session = _Session(websocket, viewer, runtime)
    await websocket.accept()
    await session.run()
