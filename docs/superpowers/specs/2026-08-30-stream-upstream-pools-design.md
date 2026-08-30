# Stream upstream pools — design

Date: 2026-08-30 · Status: approved design, awaiting implementation plan

## Goal

Let a stream forward to an **upstream pool** instead of a single
`host:port`, so TCP/UDP forwards get the same weighted balancing, failover and
per-backend tuning that proxy hosts already have. Pools gain an explicit
**context** (`http` / `stream` / `both`) that says where they may be used, and
move out of the proxy-hosts page into their own sidebar section, because once a
pool can back a stream it is no longer a proxy-host concept.

nginx has supported `upstream {}` inside `stream {}` since 1.9.0. The
open-source methods are round-robin, `least_conn`, `hash`, `random` (1.15.1) and
`least_time` (open-source from 1.31.0). Everything MegooPM's `BackendSpec`
already carries — `weight`, `max_fails`, `fail_timeout`, `backup`, `down` — is
accepted verbatim by the stream module.

## Non-goals

- Active health checks. `ngx_stream_upstream_hc_module` is NGINX Plus; MegooPM
  keeps passive checking via `max_fails` / `fail_timeout`.
- `slow_start` (NGINX Plus) and `least_time` (needs nginx ≥ 1.31 and a new enum
  member — separate change if wanted).
- `max_conns`, `resolve` and `service=` server parameters.
- Per-stream override of a pool's load-balancing method.
- Changing how proxy hosts reference pools.

## Decisions taken during brainstorming

- **A stream targets either a `host:port` or a pool, never both.** Existing
  streams keep their host/port, so there is no data migration. Rejected:
  making `upstream_id` mandatory like `ProxyHost` (forces a pool for a trivial
  one-backend forward, and needs a data migration that auto-creates a pool per
  stream); and giving streams their own backend table (duplicates the whole
  pool concept and its UI).
- **Pools carry an explicit `context`.** Rejected: sharing every pool freely and
  rejecting `ip_hash` at attach time, and silently translating `ip_hash` to
  `hash $remote_addr` in stream context — the latter makes one pool behave
  differently depending on where it is attached.
- **The `backup` + hash/ip_hash/random combination is validated now**, not left
  as a latent defect. It is the same shape of rule, in the same validator.
- **Pools move to `/upstreams`** with their own sidebar entry.
- Delivered as three independently shippable phases (see the end).

## Constraints this design is built on

Two nginx facts drive most of what follows.

**`upstream` blocks are context-local.** A pool defined in `http {}` is
invisible to `stream {}`. `nginx.conf` includes `conf.d/*.conf` into `http {}`
(non-recursively) and `conf.d/stream/*.conf` into `stream {}`, so a pool backing
a stream must be rendered into the stream directory. A `both` pool is rendered
into *both* directories under the same nginx name; that is not a collision,
because the two contexts are separate namespaces, and identical names are
precisely what a shared pool means.

**`ip_hash` does not exist in the stream module.** `app/models/enums.py`
offers it and the renderer emits `ip_hash;`, which is valid in `http` and a hard
`nginx -t` failure in `stream`. The stream near-equivalent is
`hash $remote_addr consistent`, already emitted for `hash`.

## Backend — data model

### `upstreams.context` (migration `0012_upstream_context`)

New enum type `upstream_context` with values `http`, `stream`, `both`, and:

| Column | Type | Notes |
|---|---|---|
| `context` | enum `upstream_context` | `NOT NULL DEFAULT 'http'` |

Existing rows become `http`, which preserves current behaviour exactly — every
pool that exists today is referenced by a proxy host. `UpstreamContext` is a
`StrEnum` in `app/models/enums.py` alongside `LoadBalanceMethod`, declared with
the same `values_callable` pattern.

Downgrade drops the column and the enum type.

### `streams` gains an optional pool target (migration `0013_stream_upstream`)

| Column | Change |
|---|---|
| `forward_host` | `String(255)` → nullable |
| `forward_port` | `Integer` → nullable |
| `upstream_id` | new, nullable, FK `upstreams.id` **RESTRICT**, indexed |

`RESTRICT` matches `ProxyHost.upstream_id`: a pool in use cannot be deleted.

Two constraint changes:

```sql
-- replaces forward_port_range, which rejects NULL
CHECK (forward_port IS NULL OR forward_port BETWEEN 1 AND 65535)

-- new: exactly one target
CHECK ((forward_host IS NOT NULL AND forward_port IS NOT NULL AND upstream_id IS NULL)
    OR (forward_host IS NULL     AND forward_port IS NULL     AND upstream_id IS NOT NULL))
```

Existing rows satisfy both as they stand, so **no data migration**. The
`incoming_port` uniqueness and `tcp_forwarding OR udp_forwarding` constraints
are untouched.

## Backend — validation

Six rules, all in the service layer answering **422** with a message naming the
offending field. None of them is deferred to `nginx -t`: a config error caught
there rolls back the whole apply for every object, with a generic message.

| # | Rule | Message |
|---|---|---|
| 1 | `lb_method = ip_hash` requires `context = http` | "ip_hash is not supported for TCP/UDP streams. Use hash or least_conn." |
| 2 | A `backup` backend cannot combine with `hash`, `ip_hash` or `random` | "nginx does not allow backup servers with the {method} method." |
| 3 | A stream may reference a pool only when `context ∈ {stream, both}` | "Pool '{name}' is not available for streams." |
| 4 | A proxy host or location may reference a pool only when `context ∈ {http, both}` | "Pool '{name}' is not available for proxy hosts." |
| 5 | A pool's context may not be narrowed while something references it in the context being removed | "Pool '{name}' is used by {n} stream(s); keep 'stream' or 'both'." |
| 6 | A stream sets exactly one of (`forward_host` + `forward_port`) or `upstream_id` | "Set either a forward host and port, or an upstream pool." |

Rules 1 and 2 apply to existing pools on their next save, so a pool already in a
bad state reports a clear error instead of breaking the next apply. Rule 2 in
particular will surface real misconfigurations that exist today.

Rule 5 needs a reference count over `proxy_hosts`, `proxy_host_locations` and
`streams`; it lives in `app/services/upstream.py` beside the existing
delete-guard. In phase 2 only the proxy-host half exists, since streams cannot
reference a pool until phase 3.

## Backend — nginx rendering

### Spec (`app/services/nginx/state.py`)

`DesiredState.upstreams` splits into two fields:

```python
http_upstreams: tuple[UpstreamSpec, ...] = ()
stream_upstreams: tuple[UpstreamSpec, ...] = ()
```

The rename is deliberate churn. With a single collection nothing stops
`render_config` emitting a stream-only pool into `http {}`; two fields make that
a type-level impossibility rather than a convention someone has to remember.
`UpstreamSpec` itself is unchanged — context is a routing concern, resolved by
the loader, not a rendering input.

`StreamSpec` gains `upstream_id: int | None` and its `forward_host` /
`forward_port` become `str | None` / `int | None`.

### Loader (`app/services/nginx/loader.py`)

- `http_upstreams` — pools referenced by included proxy hosts and their
  locations. Today's logic, renamed.
- `stream_upstreams` — pools referenced by included streams.
- Both are filtered to enabled pools with at least one usable backend, in id
  order, as now.
- A `both` pool referenced from each side appears in both tuples.
- **A stream whose pool has no usable backend is skipped**, mirroring the
  existing rule for a proxy host with an empty pool: emitting a `server` block
  that names a non-existent `upstream` fails `nginx -t`.

### Renderer (`app/services/nginx/renderer.py`)

`render_config` renders `state.http_upstreams` to
`megoopm-upstream-{id}.conf` as now. `render_stream_config` renders
`state.stream_upstreams` to the same filename inside the stream directory —
different directory, no collision, and `http {}`'s non-recursive include never
sees it.

A `_STREAM_LB_DIRECTIVES` map mirrors `_LB_DIRECTIVES` without `ip_hash` and
raises a `ValueError` naming the pool if `ip_hash` reaches it. Validation should
make that unreachable; a hand-edited database row must not silently emit config
that fails `nginx -t` on every node.

### Template (`app/templates/nginx/stream.conf.j2`)

`proxy_pass` becomes the pool name when the stream has one, otherwise today's
`host:port`. Nothing else in the template changes: `listen`, the `udp`
parameter, and TLS termination are all independent of the target.

## Frontend

### Pools move to their own section

- New route `app/(app)/upstreams/page.tsx` and a `primaryNav` entry titled
  "Upstream Pools" at `/upstreams` with the `Server` icon, placed after Proxy
  Hosts. `config/nav.test.ts` asserts the exact title list and must be updated.
- `components/upstreams/upstreams-view.tsx`, extracted from the pools half of
  `proxy-hosts-view.tsx`, keeping the enable toggle, edit and delete actions.
- `components/proxy-hosts/upstream-dialog.tsx` moves to `components/upstreams/`.
- `proxy-hosts-view.tsx` loses its `Tabs`, the pools table, the pools state and
  `setPoolEnabled`, becoming single-purpose again.
- The pool tests in `proxy-hosts-view.test.tsx` move to
  `upstreams-view.test.tsx`; the `openPools` tab-clicking helper disappears.

### Pool dialog

Gains a **Context** selector (`HTTP only` / `Streams only` / `Both`) with a hint
explaining that it controls where the pool may be attached. The load-balancing
method list is filtered by it: `ip_hash` is offered only for `HTTP only`, and
switching context away from HTTP while `ip_hash` is selected resets the method
to `round_robin` with an inline note rather than silently saving something the
API will reject.

`backup` is blocked inline against hash/ip_hash/random, matching rule 2.

### Stream dialog

The Details tab gains a target mode — **Single host** or **Pool**. Single host
keeps today's forward host/port inputs; Pool shows a picker listing only
stream-capable pools (`context ∈ {stream, both}`), with each entry showing its
backend count. Switching modes keeps the other mode's values in form state so
toggling back and forth does not lose typed input; only the active mode is sent.

The SSL tab is unchanged — TLS termination is independent of the target.

## Error handling

- All six validation rules return 422 with a field-specific message; the
  dialogs surface them through the existing `describeError` path.
- Deleting a pool referenced by a stream already returns 409 via the RESTRICT
  `IntegrityError` caught in `delete_upstream`, so no new code is needed — but
  the detail text is currently "Upstream is still referenced by one or more
  proxy hosts" (`routes/upstreams.py`), which becomes wrong. It, and
  `UpstreamInUseError`'s docstring, must say hosts or streams.
- A stream whose pool loses its last usable backend disappears from the rendered
  config rather than breaking the apply. This is silent by design — the same as
  proxy hosts today — and is worth a follow-up that surfaces "skipped" objects
  in the UI, but that is out of scope here.

## Testing

### Backend (pytest, Linux container)

- The new check constraints: a stream with both targets and a stream with
  neither are both rejected by the database.
- Each of the six validation rules, including rule 5's cross-context reference
  count.
- Loader: a `both` pool lands in both tuples; a stream-only pool never appears
  in `http_upstreams`; a stream whose pool has no usable backend is skipped.
- Renderer: a pooled stream emits `proxy_pass megoopm_upstream_N;` and the pool
  block is written into the stream directory; `ip_hash` in stream context
  raises.
- Migrations `0012` and `0013` up and down against real Postgres, since the
  SQLite test engine does not exercise enum types or `ALTER COLUMN DROP NOT
  NULL`.

### Frontend (vitest)

- Pool dialog: `ip_hash` absent unless context is HTTP; switching context away
  from HTTP resets the method; `backup` blocked against hash/random.
- Stream dialog: target mode switches, the picker lists only stream-capable
  pools, values survive a mode round-trip, and the payload carries exactly one
  target.
- `upstreams-view.test.tsx`: the relocated pool tests.
- `nav.test.ts`: updated title list.

## Phasing

Three independently shippable phases. The design is written as a whole so the
end state stays coherent, but each phase stands on its own.

1. **Move pools to `/upstreams`.** Pure frontend refactor — new route, nav
   entry, extracted view, relocated dialog, moved tests. No schema, no API, no
   renderer change. Ships and is reviewable on its own.
2. **Pool context and the `backup` fix.** Migration `0012`, the
   `UpstreamContext` enum, validation rules 1, 2, 4 and rule 5 over proxy
   hosts, the pool dialog's Context selector and filtered method list. Streams are
   untouched; `stream_upstreams` is added to `DesiredState` as an empty tuple so
   the renderer split lands here rather than in phase 3.
3. **Stream pool support.** Migration `0013`, `StreamSpec` and template
   changes, loader routing for `stream_upstreams`, validation rules 3, 6 and the
   stream half of 5, and the stream dialog's target mode.
