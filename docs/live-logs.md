# Live logs

An admin opens **Live logs** in the sidebar, picks one or more hosts, and
watches what nginx does for them as it happens: each request, each error, each
CrowdSec denial, each TCP/UDP connection, from every node in one timeline.
Nothing is kept: lines exist only in flight and in the open page, and closing
the page leaves no trace.

| Tab | Shows |
| --- | --- |
| Requests | One line per HTTP request: status, method, path and query, client and country, upstream, timings, bytes, user agent, redirect target, and a **CrowdSec** tag when the bouncer denied it |
| Errors | nginx's error-log lines for the host: upstream refused or timed out, body too large, TLS handshake failures, … |
| Security | Every CrowdSec denial on the picked hosts, plus every new CrowdSec decision (bans added by scenarios or by hand). Decisions are not per host |
| Stream hosts | One line per TCP/UDP connection on the picked stream hosts, **written when the connection closes** |

The host picker lists proxy, redirection and 404 hosts (stream hosts on the
Stream tab), and **Unclaimed (default site)**: requests no host claims, where
most scanners and bans land. Filters (node, status, text) run in the browser
over what has arrived. **Pause** stops rendering, not receiving. **Save what's
on screen** downloads the tab's lines from the browser; the server is not
involved.

## The flow

```
nginx                                the relay (stream server, same nginx)           Redis            any backend          browser
  access_log (one, "k":"<key>")      127.0.0.1:5140, Lua: key, NUMSUB cache,  --PUBLISH-->  megoopm:live:<key>  --SUBSCRIBE--  WebSocket  -->  page
  error_log (per block, mgm_<key>)   classify, cap, add node                                                  (per session)
                         --UDP-->
```

1. nginx sends every request line, in the `megoopm_live` JSON format, over
   UDP syslog to `127.0.0.1:5140`, through **one** access log at the `http {}`
   level of `infra/nginx/nginx.conf`. Stream lines (`megoopm_stream`) go
   through one at the `stream {}` level. The line names its host first,
   `"k":"<host key>"`, from the `set $mgm_key <key>;` that every generated
   server block (`backend/app/templates/nginx/_live_log.j2`) and the base
   default server carry.

   Each block also sends its error log at `info` to the relay. An error line
   has no variables, so the syslog tag names the host: `mgm_<host key>`. A
   block that declares an error log stops inheriting the http-level ones, so
   it restates the existing error targets (stdout, and CrowdSec's syslog when
   `CROWDSEC_SYSLOG_ADDR` is set) with
   `include /etc/nginx/logging-error-targets.conf`. It declares no access log,
   so it inherits the access targets unchanged.
2. The relay (`infra/nginx/lua/megoopm_live_relay.lua`) reads each datagram's
   host key: from `k` for an access line (whose tag is the shared `mgm_x`),
   from the tag for an error line. A line with an empty key is dropped. It
   then looks up the cached answer to `PUBSUB NUMSUB megoopm:live:<key>`
   (cached for 1 s). If nobody is subscribed, the line is dropped before it is
   decoded. Otherwise the relay drops the line unless it is shown (see
   *Errors* below), applies its per-host cap (see *Rate limits*), and
   `PUBLISH`es it.
3. The WebSocket session (`backend/app/api/routes/live_logs.py`) subscribes
   to the channels of the hosts the page watches, and forwards each message
   as a JSON frame.

**Host keys.** `p<id>` proxy host, `r<id>` redirection host, `d<id>` 404
(dead) host, `s<id>` stream host, `u` unclaimed (the default servers). The
channel is `megoopm:live:<key>` and the syslog tag `mgm_<key>`.

**What the relay publishes.**

```json
{"node": "node-a", "host": "p3", "log": "request", "sev": 6,
 "f": {"ts": "1792937730.885", "st": "200", "m": "GET", "u": "/health", "pr": "HTTP/1.1",
       "ip": "203.0.113.5", "ua": "curl/8.11.1", "in": "83", "out": "228", "rt": "0.001",
       "up": "172.22.0.7:8000", "ust": "200", "urt": "0.002", "h": "app.example.com",
       "loc": "", "cs": ""},
 "raw": null, "truncated": false}
```

- `log` is `request`, `error`, `security` or `stream`. `sev` is the syslog
  severity (nginx's level for error lines).
- `f` is the decoded access line, for `request` and `stream`. Every value is
  a string, so an empty value (`ust` on a `return`) is still valid JSON.
  Request fields:
  - `ts`: time, seconds with ms.
  - `st`: status.
  - `m`, `u`, `pr`: method, URI with the query, protocol.
  - `ip`, `ua`: client and user agent.
  - `in`, `out`: bytes received and sent.
  - `rt`: total time.
  - `up`, `ust`, `urt`: upstream address, status and time.
  - `h`: host.
  - `loc`: the `Location` sent (the redirect target).
  - `cs`: `denied` when the CrowdSec bouncer denied the request.

  Stream fields:
  - `ts`, `ip`, `st`: time, client and status.
  - `pr`: `TCP` or `UDP`.
  - `in`, `out`: bytes from and to the client.
  - `dur`: session time.
  - `up`, `uin`, `uout`: upstream address and its bytes each way.
- `raw` is the text line, for `error` and `security`. It is also the whole
  datagram when a JSON line did not decode; then `truncated` is `true`. That
  happens with a very long URL, because nginx cuts a long syslog message. An
  8 KB URL still arrives whole, and a 10 KB one is cut. The default header
  buffers already refuse a request line much past 8 KB, so this is rare.

**What the browser receives.** It receives these frames:

- `{"type": "line", …}`: the message above, plus `cc`, the client's country.
- `{"type": "decision", "value", "scope", "type_", "scenario", "duration", "origin", "cc"}`.
- `{"type": "skipped", "count": N}`: see *Rate limits*.
- `{"type": "ping"}`: every 25 s, so load balancer idle timeouts (HAProxy 60 s)
  do not cut a quiet socket.

The page sends `{"type": "watch", "hosts": ["p3", "u"], "decisions": true}` on
open and again whenever the picked hosts or tab change. The socket is not
reopened. `decisions` is true while the Security tab is open.

Log content is attacker-controlled (URLs, user agents, referrers). The page
renders it as text, never as HTML.

## Why the relay sits on loopback, inside nginx

nginx resolves a `syslog:server=` name once, when the configuration loads. If
the target were another container (`backend:5140`), `nginx -t`, reloads and
startup would fail with "host not found" whenever that container was down.
The generated configs are identical on every node, too. `127.0.0.1` always
exists, so the relay is an OpenResty stream server
(`listen 127.0.0.1:5140 udp`) in the nginx container itself. It needs no
other container to start, and under HA each node's relay hears only its own
nginx.

**Why one access log, and not one per host.** Every syslog target an
`access_log` or `error_log` directive names is its own socket in each nginx
worker. It is opened on first use and not counted in `worker_connections`. A
block that restated the CrowdSec targets and added the relay's cost four of
them per worker: about 800 per worker for 100 TLS hosts. With the access log
shared, a block costs two (its relay and CrowdSec error targets), and
`worker_rlimit_nofile 65535` leaves room for many hosts. The harness measures
it: with 150 blocks served by one worker, 4.03 sockets per block before, 2.05
after.

The relay logs only to stderr; a relay logging to itself would loop. Logging
is fire and forget for nginx: the request was served before its line was
sent. The Redis round-trip runs in a timer, off the UDP session, with 200 ms
timeouts, under `pcall`. A down or slow Redis, or a down relay, loses lines.
It never fails or slows a request, `nginx -t`, or a reload.

## "Stored nowhere"

Nothing about a live line is written anywhere:

- **No database row**, no file, no log file of its own.
- **No Redis key, list or stream.** Redis is used only for `PUBLISH`,
  `SUBSCRIBE` and `PUBSUB NUMSUB`. Pub/sub delivers a message to the
  subscribers connected at that instant and keeps nothing: a message nobody
  is subscribed to is gone.
- **No ticket record.** The WebSocket ticket is a signed token, checked by
  signature (see below).
- **`PUBSUB NUMSUB` gating.** The relay publishes a line only while its host's
  channel has a subscriber, and it learns that without anyone writing a key.
  The relay caches the count for 1 s in the shared dict `megoopm_live`, one
  per node: every nginx worker on the node reads and refreshes the same
  entry. That dict holds numbers per host key (the subscriber count, and the
  relay's per-second line and skipped counts), never lines. With nobody
  watching, a busy host costs one `NUMSUB` per second per node, and each line
  is dropped on arrival.
- **The browser** keeps the last 1,000 lines per tab, in memory. Leaving the
  page discards them.

What this does *not* change: nginx still writes its usual `combined` access
log and error log to the container's stdout/stderr, and ships them to CrowdSec
when configured, exactly as before Live logs existed. Docker's log driver keeps
those, as it always has. "Stored nowhere" is a promise about what Live logs
adds, not about nginx's own logs.

## Errors: which lines are shown

nginx sends error lines at `info`, and the relay filters them:

- A line whose message begins `[lua] <file>:<line>: …[Crowdsec] denied` is a
  **security** line (the bouncer's own log of a denial).
- A line at `warn` or above (syslog severity ≤ 4) is an **error** line.
- So is an `info` line whose message begins `SSL_do_handshake() failed` or
  `peer closed connection in SSL handshake`. nginx logs TLS handshake
  failures at `info`, and an admin chasing one needs them.

  A handshake that fails before the client has named a host (no SNI, or a
  failure before it is read) is logged by the server nginx falls back to for
  that `:443` socket. The default-TLS sites are not `default_server`, so that
  is the first `:443` server block nginx loaded, in filename order. Such a
  line shows on that host's Errors tab, often an unrelated host's.
- Everything else is dropped. Most of it is `info` noise, such as "client
  closed connection".

The markers are matched only at the start of nginx's own message, never in the
request context nginx appends (`, client: …, request: "…", referrer: "…"`).
An attacker controls that context. Without this rule, a `Referer:
[Crowdsec] denied` would turn a plain 404 into a security event, or sneak an
`info` line through.

## Who can watch, and re-checks

- **Signed-in admins in a browser only.** `POST /api/v1/live-logs/ticket`
  requires a browser session admin. **Admin API keys are refused** (403):
  lines carry client IPs and URLs that may hold tokens, and a script has the
  REST API.
- A browser cannot put a bearer header on a WebSocket. So the page swaps its
  session for a **ticket**: a JWT of type `live`, valid **30 s**, carrying the
  user id and their `token_version`. The page then opens
  `GET /api/v1/live-logs/ws?ticket=…`.
- **The ticket is in the URL, so it is in access logs.** The API server's
  access log records it: uvicorn writes
  `"WebSocket /api/v1/live-logs/ws?ticket=eyJ…" [accepted]`. So does any
  reverse proxy or load balancer in front of the API that logs URLs. The
  ticket is not one-time: it is stateless, so it can be replayed while it is
  valid. What bounds it:
  - it lives 30 s;
  - only an admin can get one;
  - it is re-checked against the database when the socket opens and every
    60 s after;
  - a password change or reset bumps `token_version`, which voids it.

  Treat those access logs as you would any log holding client IPs and URLs.
- **Re-checks.** Every 60 s the session checks that the user is still active,
  still an admin, and has the same `token_version`. If not, the socket closes
  with **4403** within 60 s. If the database cannot be reached within 10 s, it
  closes with **1011**.
- **Close codes.**

  | Code | Meaning | What the page does |
  | --- | --- | --- |
  | 4401 | Ticket missing, bad, expired, or for a user who may not watch | Gets a new ticket at once |
  | 4403 | The user stopped qualifying while the socket was open | Stops, and says so |
  | 4400 | A frame the server does not accept, such as a bad host key | Stops: a bug |
  | 1011 | The server could not re-check the session | Retries with backoff |

  On any other drop, the page reconnects with a fresh ticket. The delay
  starts at 1 s and grows to 30 s.

## Rate limits

- **300 lines per second per host, on each node, at the relay.** A busy
  watched host must not load nginx, Redis and the backend with lines the page
  would skip anyway. Over the cap the relay drops the line (before decoding
  it) and counts it, and publishes
  `{"log": "skipped", "node", "host", "count": N}` on the host's channel once
  a second. A line the relay could not hand to a timer (a full nginx timer
  queue) is counted the same way. The session adds these counts to its own.
- **200 lines per second per session**, counting decisions. Over the cap,
  lines are skipped, not queued. The session sends `{"type": "skipped",
  "count": N}` at most once a second, counting its own skips and the relay's,
  and the page shows "N lines skipped". Skipping is never silent.
- **Decisions** are read from CrowdSec LAPI every 5 s while a session watches
  Security, with the same read the Security page does. The first read only
  notes the decisions that already exist; "new" means new since the page
  started watching. A community blocklist refresh can bring thousands at
  once. The cap holds them to 200 a second, and the rest are counted as
  skipped. Every frame carries `origin`, so you can tell them apart.
- A watch can name at most 1,000 hosts.

## Stream lines, and long-lived requests, arrive on close

nginx writes an access line when a request or connection *ends*, never
when it starts. So:

- **A stream connection appears when it closes.** The Stream tab says so. A
  UDP session closes after the stream host's `proxy_timeout`.
- **A long-lived request appears when it finishes.** That covers a WebSocket
  through a proxy host, a long download or upload, and a long poll. A request
  still in progress shows nothing yet.

A "connection opened" line would need new stream Lua code, and is not built.

## Under HA

Each node's nginx sends to its own relay, and every relay publishes to the
shared Redis, tagged with that node's `NODE_ID`. The backend holding the
browser's socket, whichever node it is, subscribes there. So one page sees
every node in one timeline, with a node column and filter (hidden on a
single node).

nginx therefore needs **`NODE_ID`** under HA. `docker-compose.ha.yml` passes
it, and already refuses to start any service without it (see
[`HA-SETUP.md`](../HA-SETUP.md)). On a single node it is empty, and lines are
tagged `local`, as the backend names that node.

**Upgrading.** Each worker renders the managed nginx config once when it
starts, so existing hosts get the live log directives without anyone
changing them. Under HA, upgrade nginx on every node before the rest: the new
nginx runs the old config, but an old nginx cannot run the new one (HA-SETUP.md
§9).

## Known limits

- **"No page" ban mode and the CrowdSec tag.** With Security → Settings →
  ban page set to **No page**, the bouncer answers a bare 403. Every
  generated server block (proxy, redirection and 404 hosts, and the default
  TLS site) then serves the branded 403 error page through an internal
  redirect. nginx drops the request's Lua context there, so that request's
  line on the Requests tab carries **no CrowdSec tag**. The Security tab
  still shows the denial, with the client and host. The plain-HTTP default
  server has no branded error page, so it keeps the tag. The other ban
  modes (the MegooPM page, a custom page) answer from the bouncer itself and
  keep it.
- **A request nginx refuses before it reaches a host's directives** (a
  malformed request line, a 400) carries no host key of its own. It shows
  under **Unclaimed**, like the other requests no host claims.
- **An `access_log` in a host's advanced config** replaces the access logs that
  host inherits, as it always has for stdout and CrowdSec. Its request lines
  then leave Live logs too.
- **The relay ignores a Redis password or TLS** in `REDIS_URL`, as visitor
  analytics does. A Redis that requires auth means the relay drops every
  line. nginx says so at startup: its log carries a `WARNING: REDIS_URL …`
  line when the URL has credentials or uses `rediss://`.
- **Order.** Each line is published from its own timer, so lines from one node
  can arrive a few milliseconds out of order. Each carries `ts`.
- **Invalid UTF-8** in a URL or user agent passes through nginx's JSON
  escaping. The backend decodes it leniently (`errors="replace"`).

## How to verify

On a running stack (`docker compose -f docker-compose.dev.yml up`):

1. Sign in, open **Live logs**, pick a proxy host, and `curl` it:
   `curl -H 'Host: <domain>' http://localhost:8080/hello`. The line appears
   within a second.
2. Point a host at a dead upstream and request it. A 502 shows on Requests,
   and nginx's `connect() failed (111: Connection refused) while connecting
   to upstream` line shows on Errors.
3. On Security, run `docker compose -f docker-compose.dev.yml exec crowdsec
   cscli decisions add -i <ip> -d 10m`, with the client address step 1's line
   showed (under Docker Desktop, the network's gateway). The decision appears
   within about 5 s. Once the bouncer has refreshed (up to 10 s), your next
   request shows as a 403 with the CrowdSec tag, and as a denial on Security.
4. Pick a stream host and open and close one TCP connection: one line, when it
   closes.
5. Demote the admin from another browser. The socket closes (4403) within
   60 s.

What Redis sees, to check that nothing is stored:

```bash
docker compose -f docker-compose.dev.yml exec redis redis-cli PUBSUB NUMSUB megoopm:live:p1
docker compose -f docker-compose.dev.yml exec redis redis-cli --scan --pattern '*live*'   # nothing
docker compose -f docker-compose.dev.yml exec redis redis-cli MONITOR | grep megoopm:live
#   SUBSCRIBE / "pubsub" "numsub" / "publish" — and nothing else
```

Automated checks:

- `MSYS_NO_PATHCONV=1 bash infra/nginx/tests/live-logs.sh`: the offline
  harness. It runs two nginx nodes on one Redis, and a LAPI-only CrowdSec for
  the denial cases. It covers publishing, the node tags, hostile input,
  truncation, the 502 and its error line, stream lines, a 200-request burst,
  the sockets each server block costs a worker (150 blocks), the relay's cap
  under a 1,500-request flood, `NUMSUB` gating, the CrowdSec tag on denied
  requests only, and Redis down.
- Backend: `tests/test_live_logs.py`, `tests/test_live_logs_ws.py`,
  `tests/test_live_log_render.py`.
- Frontend: `npx vitest run src/components/live-logs`.
