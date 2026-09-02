# Dashboard events — design

## Goal

Push discrete events to the dashboard so it reflects a config apply, a
certificate change or a new ban the moment it happens, instead of up to a poll
interval later.

## Scope

This is **P4**, the last of the dashboard decomposition:

| | | |
| --- | --- | --- |
| P1 | Dashboard on data that already exists | shipped |
| P2 | Visitor analytics | shipped |
| P3 | Traffic layer on the map | shipped |
| **P4** | Realtime push | this spec |

## What this pushes, and what it does not

**Only discrete events.** Things that happen at a moment: a config apply
finishing, a certificate issued or renewed, a decision added.

**The sampled numbers keep polling.** Node metrics are written every 15 seconds
and visitor counts every 60; the traffic figure is a 15-second average by
construction. Pushing it more often would not make it fresher — it would only
move the same staleness through a different pipe.

This distinction is the whole reason P4 is worth building at all. The operator
considered pushing the entire summary and rejected it for exactly this reason:
it replaces a 15-second poll with a 15-second push and calls it realtime.

## Transport: SSE

`GET /api/v1/events`, admin-only, a `StreamingResponse` with
`media_type="text/event-stream"`.

SSE rather than WebSocket because the traffic is entirely one-directional — the
server pushes, the browser never sends. WebSocket's bidirectionality is unused
cost: a separate auth story, a separate framing protocol, and no automatic
reconnection.

### Auth is by bearer header

**Corrected after a production 401.** This spec originally had the endpoint
accept the access token from the session cookie, because `EventSource` cannot
set an `Authorization` header. It verified that the cookie is `SameSite=Lax`
and concluded the approach was safe.

That reasoning was sound and answered the wrong question. The cookie is written
with **no `Domain` attribute**, so it is *host-only*: the browser sends it only
to the host that set it. Deployed with the UI on one host and the API on
another — `lb.example` and `lb-api.example` — the cookie never reaches the API,
every connection arrives with no credential, and the endpoint correctly returns
401. Local development hides this, because the UI and API share `localhost` and
cookies ignore the port.

So the client reads the stream with **`fetch` and a `ReadableStream`**, which
can set the header, and the endpoint authenticates with the ordinary bearer
token like every other route.

What this costs: `EventSource` reconnects by itself and `fetch` does not, so
the client implements capped exponential backoff — roughly thirty lines that
were previously free.

What it removes, which is more than it costs:

- **The separate stream dependency.** No route accepts a cookie now, so the
  CSRF surface this spec worried about does not exist.
- **A CORS trap.** `cors_origins` defaults to `["*"]` while
  `allow_credentials=True`; browsers reject that pairing for *credentialed*
  requests. A bearer header is not one, so the default configuration works.

### Heartbeats

A comment frame every 20 seconds.

Two reasons. Anyone proxying the admin API through a MegooPM-managed host
inherits `proxy_read_timeout 300s`, and an idle stream would be cut; and a
client needs a liveness signal, because a TCP connection that has silently died
looks exactly like one where nothing has happened.

### Buffering

By default the admin API is published directly on `:8000` and the browser talks
to it there, so no proxy buffers the stream.

An operator who fronts the admin API with a MegooPM proxy host must set
`proxy_buffering off` for it, or nginx will hold the events until the buffer
fills. This belongs in the docs; the code cannot detect it.

## Fan-out: Redis pub/sub

Anything may publish — an API handler, a Celery task on another node — and
every connected stream relays what it receives.

This is what makes the feature work in a cluster at all, and it is nearly free:
Redis is already running for Celery, and HA already mandates that it is shared.
A browser connected to node A therefore sees an event raised on node B.

Each connected client holds one Redis subscription. The population is
administrators, not the public, so the connection count is small by nature.

## Events are signals, not payloads

An event carries **what changed, not what it changed to**:

```json
{"type": "config.applied", "at": "2026-09-02T12:00:00Z", "version": 7}
```

The client refetches through the ordinary REST path.

Carrying the data would mean a second serialisation of objects the REST API
already serialises. The two will drift the moment one is edited and the other
is not — and then the dashboard shows different numbers depending on whether
the operator arrived by poll or by push, which is a bug nobody would think to
look for. Signals keep exactly one source of truth.

It also keeps the event schema tiny and stable: adding a field to
`DashboardSummary` does not touch the event contract.

## What publishes

Three, deliberately few:

| event | raised when |
| --- | --- |
| `config.applied` | a config write completes and the version bumps |
| `certificate.changed` | a certificate is issued, renewed or fails |
| `decision.added` | a CrowdSec decision is added through MegooPM's API |

`decision.added` covers only decisions MegooPM itself creates. Bans that
CrowdSec raises on its own are invisible until the next poll, because nothing
tells MegooPM they happened. Stating that plainly matters: an operator who
believes the map is live would misread a quiet globe.

## Polling stays

**The dashboard keeps polling, at 60 seconds rather than 15.**

Push is an accelerator, never a dependency. If SSE is blocked — a corporate
proxy, an intermediary that buffers, a browser extension — the page must still
update. The failure mode of this feature is "slightly slower", not "frozen".

The longer interval is the actual saving: without push, useful freshness needed
15-second polls; with it, a 60-second poll is a backstop.

## Data flow

```
apply / cert task / decision API
        │  publish
        ▼
   Redis channel  ──────────────┐
        │                       │  (any node)
        ▼                       ▼
 GET /events (node A)    GET /events (node B)
        │                       │
        ▼                       ▼
    browser  ──refetch──>  REST endpoints
```

## Error handling

**A publish that fails is swallowed.** Raising an event is never worth failing
the operation that raised it: a certificate that renewed but could not announce
itself has still renewed.

**A dropped stream reconnects.** `EventSource` does this natively; the client
does not implement it.

**A dead Redis ends the streams and the poll carries the page.** That is the
designed fallback, not a degraded state to alert on.

## Testing

**Pure:** the event serialisation, and the SSE frame format — `data:` lines and
the blank-line terminator, which a hand-rolled formatter gets wrong in ways a
browser silently ignores by never firing an event.

**Against real Redis:** a publish reaches a subscriber, and a subscriber is not
disturbed by an event it does not care about.

**Against the API:** an unauthenticated connection is refused, and a bearer
token still works so the change does not weaken the other routes.

**Frontend:** an event triggers a refetch, and polling continues when the
stream never opens — the fallback is the point, so it is tested rather than
assumed.

**Not automatable:** whether a real browser holds the connection through a
particular proxy chain.

## Files

**Backend**

- `app/services/events.py` (new) — publish and subscribe over Redis
- `app/schemas/events.py` (new) — the event shape
- `app/api/routes/events.py` (new) — the SSE endpoint
- `app/api/deps.py` — unchanged; the stream uses the existing `AdminUser`
- `app/api/routes/_config_writes.py` — publish `config.applied`
- `app/tasks/certs.py` — publish `certificate.changed`
- `app/api/routes/crowdsec.py` — publish `decision.added`

**Frontend**

- `src/lib/events.ts` (new) — the `fetch` stream reader, frame parser and
  reconnect backoff
- `src/components/dashboard/dashboard-view.tsx` — refetch on event, poll at 60s

**Docs**

- a note that fronting the admin API with a proxy requires
  `proxy_buffering off` on the events route

## Non-goals

- **App-wide events.** One page subscribes. The stream is generic, so another
  page can subscribe later without redesigning anything.
- **Toasts.** Which events deserve interrupting someone is a product decision,
  not a transport one.
- **Pushing the summary payload.** Rejected above.
- **Replacing polling.** Explicitly kept.

## Open risks

**One Redis subscription per connected client.** Fine for administrators, and
it would not be for a public-facing stream. If the audience ever widens, this
needs a single shared subscription fanned out in-process.

**Reconnection is now the client's to get right.** `EventSource` handled it;
the backoff here is hand-written, so a bug in it shows up as a stream that
stops reconnecting after a blip. The 60-second poll is what keeps that from
being visible as a frozen page.

**Events can be missed.** A browser that reconnects between a publish and its
subscription never sees that event. The 60-second poll is what makes this
tolerable, and it is another reason polling stays.
