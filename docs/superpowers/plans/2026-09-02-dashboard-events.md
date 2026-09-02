# Dashboard Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push discrete events — a config change, a certificate change, a decision added — to the dashboard, so it reflects them immediately instead of up to a poll interval later.

**Architecture:** Publishers write to a Redis channel from anywhere (API handler or Celery task on any node); an SSE endpoint relays that channel to connected browsers. Events are signals, so the client refetches through the ordinary REST path and there is never a second serialisation to drift.

**Tech Stack:** Python 3.12, FastAPI (`StreamingResponse`), redis-py asyncio, pytest; Next.js 16, `EventSource`, vitest.

**Spec:** `docs/superpowers/specs/2026-09-02-dashboard-events-design.md`

## Global Constraints

- **Do NOT modify `get_current_user`.** The events route needs a cookie fallback because `EventSource` cannot set headers; it gets its **own** dependency. Adding the fallback to the shared dependency would extend cookie authentication to every mutating endpoint in the API and turn a read-only convenience into a CSRF surface. `SameSite=Lax` on `megoopm_session` is what makes the cookie safe here, and it only protects a read-only stream.
- **A failed publish is swallowed.** Announcing an event is never worth failing the operation that raised it: a certificate that renewed but could not announce itself has still renewed.
- **Polling stays.** The dashboard drops from 15s to 60s but never to zero. If SSE is blocked by a proxy the page must still update; the failure mode is "slower", not "frozen".
- **The event carries no payload beyond identifiers.** The client refetches. Serialising domain objects into events creates a second representation that will drift from the REST one, and then the page differs depending on how the operator arrived.
- **SSE framing is exact.** Each frame is `data: <json>\n\n`. A missing blank line means the browser never fires the event, and nothing errors — it simply looks like no events are being sent.
- Run backend tests in a Linux container — the app imports `fcntl`. The event tests need a Redis, so start one on the same network:

```bash
export MSYS_NO_PATHCONV=1
docker network create megoopm-testnet
docker run -d --name megoopm-testdb --network megoopm-testnet \
  -e POSTGRES_USER=megoopm -e POSTGRES_PASSWORD=megoopm -e POSTGRES_DB=megoopm postgres:16-alpine
docker run -d --name megoopm-testredis --network megoopm-testnet redis:7-alpine
docker run -d --name megoopm-test --user root --network megoopm-testnet \
  -v "C:/Projects/megoopm/backend:/src" -w /src \
  -e CELERY_TASK_ALWAYS_EAGER=true -e CELERY_RESULT_BACKEND=cache+memory:// \
  -e REDIS_URL="redis://megoopm-testredis:6379/0" \
  -e DATABASE_URL="postgresql+asyncpg://megoopm:megoopm@megoopm-testdb:5432/megoopm" \
  --entrypoint sleep megoopm-backend infinity
docker exec megoopm-test pip install -q "pytest>=8.2" "pytest-asyncio>=0.23" "aiosqlite>=0.20" "ruff>=0.6" "maxminddb>=2.6"
```

  Tear down with `docker rm -f megoopm-test megoopm-testdb megoopm-testredis && docker network rm megoopm-testnet`.

---

### Task 1: The event service

**Files:**
- Create: `backend/app/schemas/events.py`
- Create: `backend/app/services/events.py`
- Test: `backend/tests/test_events.py` (create)

**Interfaces:**
- Produces:
  - `Event(type: str, at: datetime, detail: dict)` in `app/schemas/events.py`
  - `format_sse(event) -> str`
  - `async publish(event) -> None`
  - `async subscribe() -> AsyncIterator[Event]`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_events.py`:

```python
"""The event channel.

The framing tests are pure and matter more than they look: a malformed SSE
frame does not error anywhere — the browser simply never fires an event, so the
feature appears to work and silently does nothing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from app.schemas.events import Event
from app.services.events import format_sse

pytestmark = pytest.mark.asyncio

AT = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


def test_a_frame_ends_with_a_blank_line() -> None:
    """Without the terminator the browser buffers forever and fires nothing."""
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
    """A newline inside the JSON would split one frame into two malformed ones."""
    frame = format_sse(
        Event(type="cert.changed", at=AT, detail={"name": "line\nbreak"})
    )
    assert frame.count("\n\n") == 1
    assert len([line for line in frame.split("\n") if line]) == 1


async def test_a_published_event_reaches_a_subscriber() -> None:
    """The whole point: a publisher and a subscriber that never met, joined by
    Redis — which is what makes this work across nodes."""
    import asyncio

    from app.services.events import publish, subscribe

    received = []

    async def listen():
        async for event in subscribe():
            received.append(event)
            return

    task = asyncio.create_task(listen())
    await asyncio.sleep(0.2)  # let the subscription register
    await publish(Event(type="config.changed", at=AT, detail={"version": 3}))
    await asyncio.wait_for(task, timeout=5)

    assert received[0].type == "config.changed"
    assert received[0].detail["version"] == 3
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_events.py -p no:cacheprovider
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.schemas.events'`.

- [ ] **Step 3: Write the schema**

Create `backend/app/schemas/events.py`:

```python
"""What a pushed event looks like.

Deliberately tiny. An event says *what changed*, never *what it changed to*:
the client refetches through the REST path, so there is exactly one
serialisation of any domain object and no second one to drift from it.

``detail`` carries identifiers only — enough for a client to decide whether it
cares, never enough to render from.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Event(BaseModel):
    type: str
    at: datetime
    detail: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 4: Write the service**

Create `backend/app/services/events.py`:

```python
"""Publishing and relaying dashboard events over Redis.

Redis rather than an in-process bus because MegooPM runs multi-node: an event
raised by a Celery task on node B must reach a browser connected to node A.
HA already mandates a shared Redis, so this costs nothing new.
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

    A publish is never worth failing the operation that raised it: a
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


__all__ = ["CHANNEL", "Event", "format_sse", "publish", "subscribe"]
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
docker exec megoopm-test python -m pytest tests/test_events.py -p no:cacheprovider
```

Expected: PASS, 4 tests. If the round-trip test hangs, the subscription had not
registered before the publish — raise the sleep, do not delete the test.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/events.py backend/app/services/events.py backend/tests/test_events.py
git commit -m "feat(events): publish and relay dashboard events over Redis"
```

---

### Task 2: The SSE endpoint

**Files:**
- Create: `backend/app/api/routes/events.py`
- Modify: `backend/app/api/deps.py`
- Modify: `backend/app/api/router.py`
- Test: `backend/tests/test_events_api.py` (create)

**Interfaces:**
- Consumes: `subscribe`, `format_sse` (Task 1).
- Produces: `GET /api/v1/events`, and `StreamAdminUser` in `deps.py`.

- [ ] **Step 1: Add the stream-only dependency**

In `backend/app/api/deps.py`, **leaving `get_current_user` untouched**:

```python
# The session cookie the frontend sets (see frontend/src/lib/auth/session.ts).
SESSION_COOKIE = "megoopm_session"


async def get_stream_user(
    request: Request,
    token: Annotated[str | None, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Resolve the user for the event stream, from the header OR the cookie.

    `EventSource` cannot set an Authorization header, so this one route accepts
    the session cookie as well. It is a SEPARATE dependency on purpose: adding
    the fallback to `get_current_user` would extend cookie authentication to
    every mutating endpoint, turning a read-only convenience into a CSRF
    surface across the whole API.

    Safe here because `megoopm_session` is `SameSite=Lax`, so a cross-origin
    `EventSource` never carries it and another site cannot open this stream.
    """
    credential = token or request.cookies.get(SESSION_COOKIE)
    if not credential:
        raise _CREDENTIALS_EXCEPTION
    try:
        payload = decode_token(credential, expected_type="access")
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise _CREDENTIALS_EXCEPTION from None

    user = await user_service.get_by_id(db, user_id)
    if user is None or not user.is_active:
        raise _CREDENTIALS_EXCEPTION
    return user


async def require_stream_admin(
    current_user: Annotated[User, Depends(get_stream_user)],
) -> User:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
        )
    return current_user


StreamAdminUser = Annotated[User, Depends(require_stream_admin)]
```

Add `Request` to the `fastapi` import and `StreamAdminUser` to `__all__`.

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_events_api.py`, reusing the `client`/`auth` fixture
pattern from `tests/test_dashboard_api.py`:

```python
async def test_the_stream_refuses_an_anonymous_connection(client) -> None:
    resp = await client.get("/api/v1/events")
    assert resp.status_code in (401, 403)


async def test_the_stream_accepts_a_bearer_token(client, auth) -> None:
    """The header must keep working: the cookie is an addition, not a swap."""
    async with client.stream("GET", "/api/v1/events", headers=auth) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")


async def test_the_stream_accepts_the_session_cookie(client, admin_token) -> None:
    """EventSource cannot set a header, so the cookie path is the one the
    browser will actually use."""
    client.cookies.set("megoopm_session", admin_token)
    async with client.stream("GET", "/api/v1/events") as resp:
        assert resp.status_code == 200


async def test_the_other_routes_still_reject_a_cookie(client, admin_token) -> None:
    """The whole point of a separate dependency: cookie auth must NOT have
    leaked into the rest of the API."""
    client.cookies.set("megoopm_session", admin_token)
    resp = await client.get("/api/v1/dashboard/summary")
    assert resp.status_code in (401, 403)
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_events_api.py -p no:cacheprovider
```

Expected: FAIL with 404 — the route does not exist. The last test may already
pass; that is fine, it is a regression guard.

- [ ] **Step 4: Write the endpoint**

Create `backend/app/api/routes/events.py`:

```python
"""The dashboard event stream (admin-only).

Server-Sent Events, not WebSocket: the traffic is one-directional, so the
bidirectionality would buy nothing and cost a separate auth story and manual
reconnection.
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
    generator on every heartbeat, which can leave it in a broken state — and
    cancelling ``queue.get()`` is safe.
    """
    async for event in subscribe():
        await queue.put(event)


async def _stream(request: Request) -> AsyncIterator[str]:
    # An immediate frame: it flushes any intermediary's buffer and tells the
    # client the stream is live rather than merely accepted.
    yield format_sse(Event(type="stream.open", at=datetime.now(UTC)))

    queue: asyncio.Queue[Event] = asyncio.Queue()
    pump = asyncio.create_task(_pump(queue))
    try:
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
            # Harmless elsewhere.
            "X-Accel-Buffering": "no",
        },
    )
```

Mount it in `app/api/router.py` with `prefix="/events"`, alongside the others.

- [ ] **Step 5: Run the tests and refresh the contract**

```bash
docker exec megoopm-test python -m pytest -p no:cacheprovider
docker exec megoopm-test python -m scripts.export_openapi
docker exec megoopm-test python -m pytest -p no:cacheprovider
docker exec megoopm-test ruff check app tests alembic
```

- [ ] **Step 6: Document the buffering requirement**

The endpoint sets `X-Accel-Buffering: no`, which nginx honours — so an operator
fronting the admin API with a MegooPM host is already covered. Other proxies
are not.

Add to `README.md`, under Deploying:

> **Event stream.** The dashboard opens an SSE connection to
> `/api/v1/events`. If you put a reverse proxy in front of the admin API, it
> must not buffer that route — nginx honours the `X-Accel-Buffering: no` header
> the endpoint sends, but other proxies may need explicit configuration. A
> buffered stream makes the dashboard fall back to its 60-second poll, which is
> a slowdown rather than a failure.

- [ ] **Step 7: Commit**

```bash
git add backend README.md
git commit -m "feat(events): an SSE endpoint for the dashboard"
```

---

### Task 3: Publish from the three sites

**Files:**
- Modify: `backend/app/api/routes/_config_writes.py`
- Modify: `backend/app/tasks/certs.py`
- Modify: `backend/app/api/routes/crowdsec.py`
- Test: `backend/tests/test_events.py`

**Interfaces:**
- Consumes: `publish`, `Event` (Task 1).

- [ ] **Step 1: Publish a config change**

In `after_config_write`, after `enqueue_nginx_reload()`:

```python
    # `config.changed`, NOT `config.applied`: this point has only enqueued the
    # reload. The apply happens later in a worker, so claiming "applied" here
    # would be a lie the dashboard would repeat. What HAS changed is the
    # database the inventory card reads, which is worth refetching now.
    await publish(
        Event(
            type="config.changed",
            at=datetime.now(UTC),
            detail={"object_type": object_type, "object_id": object_id},
        )
    )
```

- [ ] **Step 2: Publish a certificate change**

Publish from inside `_issue_async`, which is **already async** — do not reach
for `asyncio.run` in the sync task wrapper, which would nest event loops.

At the end of `_issue_async`, replacing each `return {...}` with a publish
first:

```python
async def _announce(cert_id: int, *, ok: bool) -> None:
    """Announce the outcome. Never raises: see app/services/events.publish.

    Both outcomes are announced, not just success. A failed renewal is exactly
    the thing an operator wants the dashboard to surface promptly.
    """
    await publish(
        Event(
            type="certificate.changed",
            at=datetime.now(UTC),
            detail={"cert_id": cert_id, "ok": ok},
        )
    )
```

and call `await _announce(cert_id, ok=True)` on the success path and
`await _announce(cert_id, ok=False)` wherever `_mark_failed` has run, before
returning.

- [ ] **Step 3: Publish a decision**

In the `add_decision` route in `app/api/routes/crowdsec.py`, after the decision
is created:

```python
    await publish(
        Event(type="decision.added", at=datetime.now(UTC), detail={"value": decision.value})
    )
```

- [ ] **Step 4: Add a test that a publish failure is harmless**

Append to `backend/tests/test_events.py`:

```python
async def test_a_publish_failure_does_not_raise(monkeypatch) -> None:
    """A certificate that renewed but could not announce itself has still
    renewed; the announcement must never fail the operation."""
    from app.services import events

    class Broken:
        async def publish(self, *a, **kw):
            raise OSError("redis is gone")

        async def aclose(self):
            return None

    monkeypatch.setattr(events, "_client", lambda: Broken())
    await events.publish(Event(type="x", at=AT, detail={}))  # must not raise
```

- [ ] **Step 5: Run everything**

```bash
docker exec megoopm-test python -m pytest -p no:cacheprovider
docker exec megoopm-test ruff check app tests alembic
```

Expected: all pass. Any existing test that asserts on `after_config_write`'s
behaviour must still pass — publishing is additive.

- [ ] **Step 6: Commit**

```bash
git add backend
git commit -m "feat(events): announce config, certificate and decision changes"
```

---

### Task 4: Subscribe from the dashboard

**Files:**
- Create: `frontend/src/lib/events.ts`
- Create: `frontend/src/lib/events.test.ts`
- Modify: `frontend/src/components/dashboard/dashboard-view.tsx`

**Interfaces:**
- Produces: `subscribeToEvents(onEvent: (type: string) => void): () => void`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/events.test.ts` with a fake `EventSource`:

```ts
it("calls back with the event type", () => {
  const seen: string[] = [];
  const stop = subscribeToEvents((type) => seen.push(type));

  fakeSource.emit({ type: "config.changed", at: "", detail: {} });

  expect(seen).toEqual(["config.changed"]);
  stop();
});

it("ignores a malformed frame rather than throwing", () => {
  // The stream is long-lived; one bad frame must not kill the subscription.
  const seen: string[] = [];
  subscribeToEvents((type) => seen.push(type));
  fakeSource.emitRaw("not json");
  expect(seen).toEqual([]);
});

it("closes the connection when stopped", () => {
  const stop = subscribeToEvents(() => {});
  stop();
  expect(fakeSource.closed).toBe(true);
});

it("does nothing when EventSource is unavailable", () => {
  // Older browsers and some test environments. The dashboard must still poll.
  vi.stubGlobal("EventSource", undefined);
  expect(() => subscribeToEvents(() => {})()).not.toThrow();
});
```

- [ ] **Step 2: Write the subscription**

Create `frontend/src/lib/events.ts`:

```ts
/**
 * Subscribes to the dashboard event stream.
 *
 * `EventSource` sends the session cookie automatically and reconnects on its
 * own, which is most of why SSE was chosen over WebSocket.
 *
 * Returns an unsubscribe function. Never throws: if the stream cannot be
 * opened at all, the caller's polling is what keeps the page fresh.
 */
export function subscribeToEvents(onEvent: (type: string) => void): () => void {
  if (typeof EventSource === "undefined") return () => {};

  const url = `${process.env.NEXT_PUBLIC_API_BASE_URL ?? ""}/api/v1/events`;
  let source: EventSource;
  try {
    source = new EventSource(url, { withCredentials: true });
  } catch {
    return () => {};
  }

  source.onmessage = (message) => {
    try {
      const parsed = JSON.parse(message.data) as { type?: string };
      if (parsed.type) onEvent(parsed.type);
    } catch {
      // One malformed frame must not end a long-lived subscription.
    }
  };
  // No onerror handler that closes: EventSource reconnects by itself, and
  // closing here would turn a blip into a permanently dead stream.

  return () => source.close();
}
```

- [ ] **Step 3: Wire it into the dashboard**

In `dashboard-view.tsx`:

```tsx
/**
 * The floor, not the mechanism. Pushed events refresh the page immediately;
 * this is what keeps it correct when the stream is blocked by a proxy, so it
 * must never go to zero.
 */
const POLL_MS = 60_000;
```

and the effect becomes:

```tsx
  useEffect(() => {
    void (async () => {
      await load();
    })();
    const timer = setInterval(() => void load(), POLL_MS);
    // Any event means something the dashboard shows may have changed; the
    // client refetches rather than trusting a payload, so the type is only a
    // trigger.
    const unsubscribe = subscribeToEvents(() => void load());
    return () => {
      clearInterval(timer);
      unsubscribe();
    };
  }, [load]);
```

Both cleanups matter: a leaked `EventSource` holds a connection open per mount,
and the dashboard is a page an operator navigates in and out of all day.

- [ ] **Step 4: Run the full frontend gate**

```bash
cd frontend && npx vitest run && npm run typecheck && npm run lint && npm run build
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(dashboard): refresh on pushed events, poll as the floor"
```

---

## Manual verification

Not reachable by any automated test — and this feature depends more than most
on a real browser and a real proxy chain:

1. Open the dashboard with devtools on the Network tab. Confirm `/api/v1/events`
   opens and stays pending rather than completing.
2. Wait a minute; confirm keepalive comments arrive and the connection survives.
3. In another tab, disable a proxy host. The dashboard's inventory card should
   update **without waiting for the 60-second poll**.
4. Stop Redis. Confirm the dashboard keeps updating on its poll — the failure
   mode must be slower, not frozen.
5. Navigate away from the dashboard and back several times, then check the
   backend's connection count. A leak here shows up as connections that never
   close.
6. If you front the admin API with a MegooPM proxy host, repeat step 1 through
   it: without `proxy_buffering off`, events arrive in bursts or not at all.
