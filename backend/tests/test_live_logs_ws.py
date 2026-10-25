"""The Live logs ticket and WebSocket.

**Fakes, and why they are enough.** The socket's moving parts are injected
through ``get_live_runtime``: a fake clock the test advances by hand, a fake
pub/sub that delivers only to channels it was subscribed to (as Redis does),
and a fake decisions source. Nothing here needs Redis or CrowdSec. The users
live in a real SQLite file, read through the same loader production uses, so a
demotion is a real ``UPDATE`` that the 60 s re-check has to notice.

**Threads.** Starlette's ``TestClient`` runs the app on its own event loop in
another thread. The fakes are therefore thread-safe, and every receive here has
a timeout, so a regression fails the test instead of hanging the run.
"""

from __future__ import annotations

import asyncio
import json
import threading
import warnings
from collections import deque
from datetime import timedelta
from typing import Any

import anyio
import pytest
from app.core.security import (
    _create_token,
    create_access_token,
    create_live_ticket,
    decode_token,
)
from app.main import app
from app.models.user import User, UserRole
from app.schemas.crowdsec import Decision
from app.services import api_keys, live_logs
from app.services import user as user_service
from app.services.live_logs import LiveRuntime, get_live_runtime
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.exceptions import StarletteDeprecationWarning
from starlette.websockets import WebSocketDisconnect

with warnings.catch_warnings():
    # Starlette asks for httpx2; the installed httpx works and is what the
    # rest of the suite uses.
    warnings.simplefilter("ignore", StarletteDeprecationWarning)
    from starlette.testclient import TestClient

WS = "/api/v1/live-logs/ws"
RECEIVE_TIMEOUT = 5.0


# --- Fakes -------------------------------------------------------------------


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def fast_sleep(_seconds: float) -> None:
    """Every tick is due at once in fake time; yield briefly in real time."""
    await asyncio.sleep(0.002)


class FakePubSub:
    def __init__(self, hub: FakeHub) -> None:
        self.hub = hub
        self.subscribed: set[str] = set()
        self.queue: deque[tuple[str, bytes]] = deque()
        # True once the reader has come back for more and found nothing: every
        # message handed out before then has been fully handled.
        self.idle = True

    async def subscribe(self, *channels: str) -> None:
        with self.hub.cond:
            self.subscribed |= set(channels)
            self.hub.cond.notify_all()

    async def unsubscribe(self, *channels: str) -> None:
        with self.hub.cond:
            self.subscribed -= set(channels)
            self.hub.cond.notify_all()

    async def get_message(self, *, timeout: float) -> dict[str, Any] | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + min(timeout, 0.05)
        while True:
            with self.hub.cond:
                if self.queue:
                    channel, data = self.queue.popleft()
                    return {"type": "message", "pattern": None, "channel": channel, "data": data}
                self.idle = True
                self.hub.cond.notify_all()
            if loop.time() >= deadline:
                return None
            await asyncio.sleep(0.002)

    async def aclose(self) -> None:
        with self.hub.cond:
            self.hub.open_subs.remove(self)
            self.hub.cond.notify_all()


class FakeHub:
    """Stands in for Redis pub/sub: delivers only to subscribed channels."""

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.open_subs: list[FakePubSub] = []
        self.opened = 0
        self.fail_next_opens = 0

    def open(self) -> FakePubSub:
        with self.cond:
            self.opened += 1
            if self.fail_next_opens:
                self.fail_next_opens -= 1
                raise ConnectionError("redis is down")
            sub = FakePubSub(self)
            self.open_subs.append(sub)
            return sub

    def channels(self) -> set[str]:
        with self.cond:
            return set().union(*(s.subscribed for s in self.open_subs))

    def wait_channels(self, expected: set[str]) -> None:
        with self.cond:
            ok = self.cond.wait_for(
                lambda: set().union(*(s.subscribed for s in self.open_subs)) == expected,
                timeout=RECEIVE_TIMEOUT,
            )
        assert ok, f"subscribed to {self.channels()}, expected {expected}"

    def publish(self, key: str, data: bytes | dict) -> int:
        channel = f"megoopm:live:{key}"
        if isinstance(data, dict):
            data = json.dumps(data).encode()
        with self.cond:
            receivers = [s for s in self.open_subs if channel in s.subscribed]
            for sub in receivers:
                sub.queue.append((channel, data))
                sub.idle = False
            return len(receivers)

    def wait_drained(self) -> None:
        with self.cond:
            ok = self.cond.wait_for(
                lambda: all(s.idle and not s.queue for s in self.open_subs),
                timeout=RECEIVE_TIMEOUT,
            )
        assert ok, "the socket never finished reading what was published"


class FakeDecisions:
    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.items: list[Decision] = []
        self.calls = 0

    async def __call__(self) -> list[Decision]:
        with self.cond:
            self.calls += 1
            self.cond.notify_all()
            return list(self.items)

    def wait_calls(self, n: int) -> None:
        with self.cond:
            ok = self.cond.wait_for(lambda: self.calls >= n, timeout=RECEIVE_TIMEOUT)
        assert ok, f"decisions were read {self.calls} times, expected {n}"


# --- Fixtures ----------------------------------------------------------------


class Live:
    def __init__(self, factory: async_sessionmaker) -> None:
        self.factory = factory
        self.hub = FakeHub()
        self.clock = FakeClock()
        self.decisions = FakeDecisions()
        self.runtime = LiveRuntime(
            clock=self.clock,
            sleep=fast_sleep,
            open_pubsub=self.hub.open,
            session_factory=factory,
            list_decisions=self.decisions,
        )
        self.client = TestClient(app)

    def add_user(self, email: str, role: UserRole = UserRole.admin) -> User:
        async def _add() -> User:
            async with self.factory() as db:
                return await user_service.create_user(
                    db, email=email, password="password123", full_name=email, role=role
                )

        return asyncio.run(_add())

    def update_user(self, user_id: int, **values: Any) -> None:
        async def _update() -> None:
            async with self.factory() as db:
                user = await db.get(User, user_id)
                for name, value in values.items():
                    setattr(user, name, value)
                await db.commit()

        asyncio.run(_update())

    def connect(self, ticket: str | None):
        url = WS if ticket is None else f"{WS}?ticket={ticket}"
        return self.client.websocket_connect(url)


@pytest.fixture
def live(tmp_path, monkeypatch):
    # A file, not :memory:, and NullPool: the test thread and the app's thread
    # each run their own event loop, and neither may borrow the other's
    # connection.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'live.db'}", poolclass=NullPool)

    async def _create() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(User.metadata.create_all, tables=[User.__table__])

    asyncio.run(_create())
    harness = Live(async_sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(
        live_logs,
        "country_for",
        lambda scope, value: "DE" if (scope, value) == ("Ip", "203.0.113.9") else None,
    )
    app.dependency_overrides[get_live_runtime] = lambda: harness.runtime
    try:
        yield harness
    finally:
        app.dependency_overrides.pop(get_live_runtime, None)
        asyncio.run(engine.dispose())


@pytest.fixture
def admin(live: Live) -> User:
    return live.add_user("admin@example.com")


def ticket_for(user: User) -> str:
    return create_live_ticket(user.id, token_version=user.token_version)


# --- Receiving with a timeout --------------------------------------------------


def receive(ws) -> dict[str, Any]:
    """The next message the server sent, or fail after RECEIVE_TIMEOUT."""

    async def _get() -> dict[str, Any]:
        with anyio.fail_after(RECEIVE_TIMEOUT):
            return await ws._send_rx.receive()

    return ws.portal.call(_get)


def next_frame(ws, *, skip: tuple[str, ...] = ("ping",)) -> dict[str, Any]:
    while True:
        message = receive(ws)
        if message["type"] == "websocket.close":
            raise WebSocketDisconnect(code=message.get("code", 1000))
        frame = json.loads(message["text"])
        if frame.get("type") not in skip:
            return frame


def close_code(ws) -> int:
    """Read until the server closes; anything sent before that is ignored."""
    while True:
        message = receive(ws)
        if message["type"] == "websocket.close":
            return message.get("code", 1000)


def watch(ws, hosts: list[str], decisions: bool = False) -> None:
    ws.send_json({"type": "watch", "hosts": hosts, "decisions": decisions})


def request_line(host: str = "p1", ip: str = "203.0.113.9", **f: str) -> dict[str, Any]:
    fields = {"ip": ip, "st": "200", "m": "GET", "u": "/", "ua": "curl/8", "cs": ""}
    fields.update(f)
    return {
        "node": "node-a",
        "host": host,
        "log": "request",
        "sev": 6,
        "f": fields,
        "raw": None,
        "truncated": False,
    }


# --- The ticket -------------------------------------------------------------


async def test_an_api_key_cannot_get_a_ticket(
    db_client: AsyncClient, admin_user: User, session_factory
) -> None:
    async with session_factory() as db:
        owner = await db.get(User, admin_user.id)
        _, key = await api_keys.create(db, owner, name="CI", expires_at=None)

    resp = await db_client.post(
        "/api/v1/live-logs/ticket", headers={"Authorization": f"Bearer {key}"}
    )

    assert resp.status_code == 403, resp.text


async def test_a_member_cannot_get_a_ticket(db_client: AsyncClient, member_token: str) -> None:
    resp = await db_client.post(
        "/api/v1/live-logs/ticket", headers={"Authorization": f"Bearer {member_token}"}
    )
    assert resp.status_code == 403


async def test_a_signed_in_admin_gets_a_short_ticket(
    db_client: AsyncClient, admin_token: str, admin_user: User
) -> None:
    resp = await db_client.post(
        "/api/v1/live-logs/ticket", headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["expires_in"] == 30
    payload = decode_token(body["ticket"], expected_type="live")
    assert payload["sub"] == str(admin_user.id)
    assert payload["tv"] == admin_user.token_version


# --- Opening the socket --------------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "garbage",
        "expired",
        "access_token",
        "member",
        "disabled",
        "stale_token_version",
        "unknown_user",
    ],
)
def test_a_bad_or_expired_ticket_is_refused(live: Live, admin: User, kind: str) -> None:
    member = live.add_user("member@example.com", UserRole.member)
    disabled = live.add_user("gone@example.com")
    live.update_user(disabled.id, is_active=False)
    ticket = {
        "missing": None,
        "garbage": "not-a-jwt",
        "expired": _create_token(
            str(admin.id), "live", timedelta(seconds=-1), {"tv": admin.token_version}
        ),
        "access_token": create_access_token(
            admin.id, admin.role.value, token_version=admin.token_version
        ),
        "member": ticket_for(member),
        "disabled": ticket_for(disabled),
        "stale_token_version": create_live_ticket(admin.id, token_version=admin.token_version + 1),
        "unknown_user": create_live_ticket(9999, token_version=0),
    }[kind]

    with live.connect(ticket) as ws:
        # The first thing the server sends is the close: no frame, no data.
        message = receive(ws)

    assert message["type"] == "websocket.close"
    assert message["code"] == 4401
    assert live.hub.opened == 0


# --- Forwarding ---------------------------------------------------------------


def test_watched_lines_are_forwarded_with_the_country(live: Live, admin: User) -> None:
    with live.connect(ticket_for(admin)) as ws:
        watch(ws, ["p1"])
        live.hub.wait_channels({"megoopm:live:p1"})

        line = request_line()
        error = {
            "node": "node-b",
            "host": "p1",
            "log": "error",
            "sev": 3,
            "f": None,
            "raw": "connect() failed (111: Connection refused)",
            "truncated": False,
        }
        live.hub.publish("p1", b"not json, from someone else on the channel")
        live.hub.publish("p1", line)
        live.hub.publish("p1", error)
        hostile = (
            b'{"node":"node-a","host":"p1","log":"request","sev":6,'
            b'"f":{"ip":"198.51.100.7","ua":"<img src=x onerror=alert(1)> \xff\\ud800"},'
            b'"raw":null,"truncated":false}'
        )
        live.hub.publish("p1", hostile)

        assert next_frame(ws) == {**line, "type": "line", "cc": "DE"}
        assert next_frame(ws) == {**error, "type": "line", "cc": None}
        hostile_frame = next_frame(ws)
        assert hostile_frame["f"]["ua"] == "<img src=x onerror=alert(1)> �\ud800"
        assert hostile_frame["cc"] is None

    assert live.decisions.calls == 0


def test_unwatched_hosts_are_not_subscribed(live: Live, admin: User) -> None:
    with live.connect(ticket_for(admin)) as ws:
        watch(ws, ["p1", "u"])
        live.hub.wait_channels({"megoopm:live:p1", "megoopm:live:u"})

        assert live.hub.publish("p2", request_line("p2")) == 0
        live.hub.publish("p1", request_line("p1"))
        assert next_frame(ws)["host"] == "p1"

        # Changing the watch needs no reconnect, and drops the old channels.
        watch(ws, ["p2"])
        live.hub.wait_channels({"megoopm:live:p2"})
        assert live.hub.publish("p1", request_line("p1")) == 0
        live.hub.publish("p2", request_line("p2"))
        assert next_frame(ws)["host"] == "p2"

        watch(ws, [])
        live.hub.wait_channels(set())


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "watch", "hosts": ["p1;flushall"]},
        {"type": "watch", "hosts": ["p1", "x1"]},
        {"type": "watch", "hosts": [""]},
        {"type": "watch", "hosts": [1]},
        {"type": "watch", "hosts": "p1"},
        {"type": "watch", "hosts": ["p1"], "decisions": "yes"},
        {"type": "watch", "hosts": [f"p{i}" for i in range(1, 1003)]},
        {"type": "subscribe", "hosts": ["p1"]},
        ["p1"],
        "not json",
    ],
)
def test_a_watch_frame_with_a_bad_host_key_is_refused(live: Live, admin: User, frame) -> None:
    with live.connect(ticket_for(admin)) as ws:
        if isinstance(frame, str):
            ws.send_text(frame)
        else:
            ws.send_json(frame)
        assert close_code(ws) == 4400

    assert live.hub.channels() == set()


# --- The session re-check -----------------------------------------------------


def _disconnects_after(live: Live, admin: User, **change: Any) -> None:
    with live.connect(ticket_for(admin)) as ws:
        watch(ws, ["p1"])
        live.hub.wait_channels({"megoopm:live:p1"})

        # A minute passes with nothing changed: the check runs and passes, and
        # the socket keeps sending (the ping proves the tick got that far).
        live.clock.advance(60)
        assert receive(ws)["type"] == "websocket.send"

        live.update_user(admin.id, **change)
        live.clock.advance(60)
        assert close_code(ws) == 4403

    # The socket's subscriptions went with it.
    assert live.hub.open_subs == []


def test_a_demoted_admin_is_disconnected_within_a_minute(live: Live, admin: User) -> None:
    _disconnects_after(live, admin, role=UserRole.member)


def test_a_bumped_token_version_disconnects(live: Live, admin: User) -> None:
    _disconnects_after(live, admin, token_version=admin.token_version + 1)


def test_a_disabled_admin_is_disconnected(live: Live, admin: User) -> None:
    _disconnects_after(live, admin, is_active=False)


def test_pings_every_25_seconds(live: Live, admin: User) -> None:
    with live.connect(ticket_for(admin)) as ws:
        live.clock.advance(25)
        assert next_frame(ws, skip=()) == {"type": "ping"}


# --- Backpressure -----------------------------------------------------------


def test_a_flood_yields_a_skipped_frame(live: Live, admin: User) -> None:
    with live.connect(ticket_for(admin)) as ws:
        watch(ws, ["p1"])
        live.hub.wait_channels({"megoopm:live:p1"})

        # 250 lines inside one (frozen) second.
        for i in range(250):
            live.hub.publish("p1", request_line(u=f"/{i}"))
        live.hub.wait_drained()

        lines = [next_frame(ws) for _ in range(200)]
        assert all(frame["type"] == "line" for frame in lines)
        assert [frame["f"]["u"] for frame in lines] == [f"/{i}" for i in range(200)]

        # The burst is reported once, a second later, as one frame.
        live.clock.advance(1)
        assert next_frame(ws) == {"type": "skipped", "count": 50}

        # And the cap is per second: the next second's lines go through.
        live.hub.publish("p1", request_line(u="/after"))
        assert next_frame(ws)["f"]["u"] == "/after"


RELAY_SKIPPED = b'{"log":"skipped","node":"node-a","host":"p1","count":300}'


def test_the_relays_skipped_count_joins_the_sessions_own(live: Live, admin: User) -> None:
    with live.connect(ticket_for(admin)) as ws:
        watch(ws, ["p1"])
        live.hub.wait_channels({"megoopm:live:p1"})

        # The session's own cap skips one line; the relay reports 300 it never
        # sent. The relay's message is a count, so the cap does not apply to it.
        for i in range(201):
            live.hub.publish("p1", request_line(u=f"/{i}"))
        live.hub.publish("p1", RELAY_SKIPPED)
        live.hub.wait_drained()
        assert all(next_frame(ws)["type"] == "line" for _ in range(200))

        live.clock.advance(1)
        assert next_frame(ws) == {"type": "skipped", "count": 301}


def test_the_relays_skipped_message_is_never_a_line(live: Live, admin: User) -> None:
    with live.connect(ticket_for(admin)) as ws:
        watch(ws, ["p1"])
        live.hub.wait_channels({"megoopm:live:p1"})

        live.hub.publish("p1", RELAY_SKIPPED)
        live.hub.publish("p1", request_line(u="/after"))
        live.hub.wait_drained()
        assert next_frame(ws)["f"]["u"] == "/after"

        live.clock.advance(1)
        assert next_frame(ws) == {"type": "skipped", "count": 300}


# --- Decisions ------------------------------------------------------------------


def _decision(id_: int, value: str) -> Decision:
    return Decision(
        id=id_,
        type="ban",
        scope="Ip",
        value=value,
        duration="4h",
        origin="crowdsec",
        scenario="crowdsecurity/http-probing",
    )


def test_decisions_are_sent_once(live: Live, admin: User) -> None:
    old = _decision(1, "192.0.2.1")
    new = _decision(2, "203.0.113.9")
    newer = _decision(3, "198.51.100.7")
    live.decisions.items = [old]

    with live.connect(ticket_for(admin)) as ws:
        watch(ws, [], decisions=True)
        # The first read only learns what already exists: a page opened on a
        # server with 15,000 community bans must not receive 15,000 frames.
        live.decisions.wait_calls(1)

        live.decisions.items = [old, new]
        live.clock.advance(5)
        assert next_frame(ws) == {
            "type": "decision",
            "value": "203.0.113.9",
            "scope": "Ip",
            "type_": "ban",
            "scenario": "crowdsecurity/http-probing",
            "duration": "4h",
            "origin": "crowdsec",
            "cc": "DE",
        }

        # Read again with `new` still active: only the newer one arrives.
        live.decisions.items = [old, new, newer]
        live.clock.advance(5)
        assert next_frame(ws)["value"] == "198.51.100.7"


def test_decisions_stop_when_unwatched(live: Live, admin: User) -> None:
    with live.connect(ticket_for(admin)) as ws:
        watch(ws, [], decisions=True)
        live.decisions.wait_calls(1)
        watch(ws, ["p1"], decisions=False)
        live.hub.wait_channels({"megoopm:live:p1"})
        calls = live.decisions.calls

        live.clock.advance(30)
        assert next_frame(ws, skip=()) == {"type": "ping"}
        assert live.decisions.calls == calls


# --- Redis down -----------------------------------------------------------------


def test_redis_down_does_not_end_the_socket(live: Live, admin: User) -> None:
    live.hub.fail_next_opens = 2

    with live.connect(ticket_for(admin)) as ws:
        watch(ws, ["p1"])
        live.hub.wait_channels({"megoopm:live:p1"})
        live.hub.publish("p1", request_line())
        assert next_frame(ws)["type"] == "line"

    assert live.hub.opened == 3
