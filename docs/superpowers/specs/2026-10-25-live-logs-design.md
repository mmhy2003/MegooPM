# Live logs — design

**Status:** awaiting review, 2026-10-25. Layout, log kinds, transport, the host
list and stream timing were agreed in the brainstorm.

## Intent

An admin opens one page and watches, as it happens, what nginx is doing for
the hosts they care about: each request, each error, each security block —
from every node at once, in one timeline. Nothing about it is kept: the page
shows lines in flight, and closing it leaves no trace anywhere.

What the user asked for, in substance:

- see logs live for a chosen proxy host or redirection host, and recommend
  what else could be shown live;
- over **WebSockets**;
- **no storage** — not in the database, not in a cache, not in files — lines
  are taken live from nginx and pushed to the UI immediately;
- under HA, lines from **every node** shown in one place.

Decisions taken during the brainstorm:

1. **Layout A** (approved mockup:
   `.superpowers/brainstorm/407-1792928043/content/live-logs-layouts.html`):
   a **Live logs** page in the sidebar. Pick one or more hosts (proxy,
   redirection and dead hosts, plus **Unclaimed (default site)** for requests
   no host claims — where most scanners and bans land), filter by node,
   status and free text, switch
   between log kinds with tabs, pause, see the rate, click a line for every
   field.
2. **Log kinds:** **Requests**, **Errors**, **Security**, and **Stream hosts**
   (TCP/UDP connections). Not System events, not admin activity.
3. **Transport:** nginx on each node sends lines over UDP syslog to a relay
   on that node; the relay publishes only watched lines to a Redis pub/sub
   channel; the backend holding the browser's WebSocket forwards them. Approved with the explicit understanding that Redis here is a
   pass-through — a channel, not a key, list or stream — and keeps nothing.
   (Diagram: `.superpowers/brainstorm/407-1792928043/content/live-logs-flow.html`;
   it draws the relay inside the backend — superseded, see *The relay*.)
4. **Stream connections, and long-lived requests, appear when they close.**
   nginx writes those lines only at the end; a "connection opened" line would
   need new stream-Lua code and is not built. The Stream tab says so.

Success looks like: a Nextcloud upload fails; the admin opens Live logs,
picks the host, retries the upload, and sees the 403 with a WAF tag — or the
502 and, on the Errors tab, nginx's "upstream timed out" line — with the node
that served it, within a second.

## Goals

1. A **Live logs** page, admin-only, in the sidebar, as in the mockup.
2. **Requests** tab: one line per HTTP request on the selected proxy and
   redirection hosts: time (ms), node, status, method, path and query,
   client IP with country flag, upstream address, upstream status and time,
   total time, bytes in/out, user agent, redirect target for redirection
   hosts, and a tag when CrowdSec or the WAF denied it. Click a line: every
   field in a detail box (mockup B's box, shown under the list in A).
3. **Errors** tab: nginx's error-log lines for the selected hosts —
   upstream refused/timed out, TLS handshake failures, body too large,
   AppSec timeouts — with node and time. Everything at `warn` and above,
   plus TLS handshake failures, which nginx logs at `info`.
4. **Security** tab: every CrowdSec denial on the selected hosts as it
   happens (bouncer ban or AppSec/WAF block: client, rule/scenario when
   known, host, node), plus CrowdSec's new decisions (bans added by
   scenarios or by hand) as they appear, which are not per host.
5. **Stream hosts** tab: one line per TCP/UDP connection on the selected
   stream hosts, written when the connection closes: time, node, client,
   upstream, bytes each way, duration, status. The tab says lines appear on
   close (a UDP session closes after `proxy_timeout`).
6. **All nodes, one timeline**, each line tagged with its node, and a node
   filter. Single-node shows no node column.
7. **Nothing stored.** No database row, no Redis key/list/stream, no file.
   The browser keeps the last **1,000 lines per tab** in memory; leaving the
   page discards them.
8. **No cost when nobody watches**, beyond nginx emitting UDP datagrams to
   the local relay, which drops them.

## Non-goals

- History, search over the past, retention, export to a log system.
- System events (applies, reloads, certificate steps) and admin activity —
  recommended in the brainstorm, not chosen.
- Per-host log files on disk, or changing what nginx writes to stdout and to
  CrowdSec today.
- Alerting or notifications from log content.

## Anti-goals

- **Anything that can slow or fail a real request.** Logging is fire and
  forget: a down or slow relay must cost nginx nothing.
- **A log line that outlives the moment**, in any store, on any node.
- **A browser that freezes** under a busy host. Rate limiting must be
  visible ("N lines skipped"), never silent.
- **Leaking lines to someone who shouldn't see them.** Request lines carry
  client IPs and URLs that may contain tokens.

## Constraints

- **nginx today** writes one shared access log (`combined`, no host field) to
  stdout and, when `CROWDSEC_SYSLOG_ADDR` is set, to CrowdSec over syslog.
  Both must keep working unchanged: CrowdSec's nginx parsers read the
  `combined` format.
- **nginx does not know its node id** today; the backend does (`NODE_ID` /
  `settings.effective_node_id`). The nginx container must be given it (env)
  so the relay can tag lines.
- **nginx resolves a `syslog:server=` name once, at config load.** A target
  named after another container (`backend`) makes `nginx -t`, reloads and
  startup fail with "host not found" whenever that container is down — and
  the managed configs are identical on every node. The target must be an IP
  literal that is always there.
- **HA**: every node runs nginx and a backend, on its own compose network,
  with one shared Redis. The browser reaches *some* node's backend through
  the load balancer.
- **Browsers cannot set headers on a WebSocket.** The API authenticates by
  bearer header today, and the events route documents why a cookie was not
  an option. The WebSocket needs another way to carry auth (see *Left to
  the builder*).
- `uvicorn[standard]` is already a dependency, so FastAPI WebSockets work.
- The existing dashboard live channel is SSE over Redis pub/sub
  (`app/services/events.py`); it stays as is. Live logs are a separate
  WebSocket, as asked.

## How it works

### nginx emits

- Every proxy, redirection, dead and default server block, and every stream
  server, gets an additional log target: **UDP syslog to the relay at
  `127.0.0.1:<port>`**, in a **MegooPM JSON log format**, tagged so the relay
  knows the host's **kind and id** (the default server is the "unclaimed"
  pseudo-host). The existing `combined` stdout and CrowdSec targets are
  untouched — a server block that declares its own `access_log` stops
  inheriting the http-level ones, so it must restate them.
- Errors: a per-server `error_log` to the same relay, tagged the same way,
  at **`info`**; the relay keeps `warn` and above plus TLS handshake failures
  (`SSL_do_handshake() failed`, `peer closed connection in SSL handshake`,
  and similar) and drops the rest on arrival — `info` also carries a line per
  "client closed connection". *My call:* the cost is loopback UDP datagrams
  for every host all the time.
- Stream: a `log_format` and `access_log` in the `stream {}` context (stream
  has none today) with the stream variables (client, upstream, bytes, session
  time, status).
- Security: the Lua bouncer already logs every denial (`[Crowdsec] denied
  '<ip>' with 'ban' (by appsec|lapi)`) to the error log; those lines arrive
  with the Errors stream and are classified as Security by the relay. Where
  the request line itself was denied, the Requests line carries the tag.

### The relay (per node, inside nginx)

*My call*, replacing "inside the backend" from the brainstorm diagram: the
relay is an OpenResty **stream UDP server bound to `127.0.0.1`** in the nginx
container itself, with a Lua handler. Loopback always resolves, so no other
container being down can break `nginx -t` or a reload, and nginx's Lua can
already reach Redis (the visitor analytics use it). Under HA each node's
relay hears only its own nginx. Care is needed so the relay's own errors do
not loop back into it (its server block logs nowhere, or to stderr only).

- It parses each datagram, adds `node`, and decides whether anyone is
  watching that host and kind.
- "Is anyone watching" is answered without storing anything: per node, the
  relay subscribes to a small control channel on which WebSocket sessions
  announce what they watch, and keeps the set **in its own process memory**;
  or it asks Redis for the subscriber count of the host's channel (`PUBSUB
  NUMSUB`). Either way no key is written. *Builder's choice; see below.*
- Watched lines are published to a per-host Redis channel
  (`megoopm:live:<kind>:<id>`). Unwatched lines are dropped on arrival.

### The WebSocket (any node)

- `GET /api/v1/live-logs/ws`, for **signed-in admins in a browser only** —
  not admin API keys (*my call*: lines carry client IPs and URLs that may hold
  tokens, and a script has the REST API). The session is re-checked while
  the socket is open (*my call:* every 60 s): disabled, demoted or signed
  out closes it. The client sends what it watches
  (hosts, kinds) and can change it without reconnecting; the server
  subscribes to the matching channels and forwards each message as a JSON
  frame.
- CrowdSec's new decisions (Security tab) come from the backend asking LAPI
  for new decisions on a short interval while at least one session watches
  Security — the same read the Security page does, nothing stored.
- **Backpressure:** per session, a cap on lines per second (*my call:* 200);
  over the cap, lines are skipped and a `{"skipped": N}` frame tells the UI,
  which shows it inline.

### The page

- Built from the approved mockup A, in the existing UI kit. Host picker lists
  proxy and redirection hosts (and stream hosts on the Stream tab). Filters
  run in the browser over what has arrived.
- **Pause** stops rendering, not receiving: lines that arrive while paused
  count toward the 1,000 and show as "N new" on resume.
- **Save what's on screen** (*my call, kept from the mockup*): downloads the
  lines currently in the tab's memory as a text file, from the browser; the
  server stores nothing.

## Left to the builder

- WebSocket authentication for a browser session: e.g. a short-lived one-time
  ticket from an authenticated `POST` passed in the query string, or the
  token in the first message; it must not rely on the host-only session
  cookie. Heartbeats so load balancers' idle timeouts (HAProxy 60 s) don't
  cut a quiet socket.
- How the relay learns what is watched (control channel vs `PUBSUB NUMSUB`,
  with a per-worker cache so a busy host doesn't ask Redis per line), the
  relay's UDP port, and the JSON log format's exact fields and escaping
  (`escape=json`).
- How each server block tags its lines (syslog `tag=`, or a field in the
  JSON).
- The decisions polling interval, and the exact backpressure numbers.
- Test layout, following existing patterns (renderer tests for the new log
  directives, relay parsing tests, WebSocket tests with fakes, UI tests).
