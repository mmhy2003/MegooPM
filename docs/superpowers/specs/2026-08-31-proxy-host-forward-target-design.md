# Proxy host forward target: single host or pool — design

Date: 2026-08-31 · Status: approved design, awaiting implementation plan

## Goal

Let a proxy host — and each of its extra locations — forward to a single
`host:port` instead of requiring an upstream pool, the same either/or streams
gained in `2026-08-30-stream-upstream-pools-design.md`. A pool stays the
default for a new host; a single backend no longer costs a pool to express.

## Non-goals

- Removing pools, or changing anything about how a pool-targeted host renders.
- Per-location certificates, access lists or toggles — unchanged.
- Migrating existing pool-targeted hosts to single hosts, or offering to
  "collapse" a one-backend pool into a host target.
- Health checking a single host target. nginx gives a bare `proxy_pass` no
  passive failover, because there is nothing to fail over to; that is inherent
  to the choice, not a gap to fill.

## Decisions taken during brainstorming

- **Locations get the same either/or**, not just the root route. A user who
  picked a single host for `/` will expect the same choice for `/api`; offering
  the switch on one row and not the rows beneath it reads as an oversight.
- **A new host's dialog opens on Pool**, preserving today's behaviour exactly and
  keeping pools the path of least resistance. Rejected: defaulting to Single
  host (changes an existing surface's default and nudges people away from
  pools), and remembering the last choice per user (hidden state that makes the
  dialog behave differently for two operators on one install).
- Delivered in two phases: root route first, then locations.

## A positioning note

`app/models/upstream.py`'s module docstring currently reads:

> The defining feature of MegooPM over stock Nginx Proxy Manager: a proxy host
> forwards to an `Upstream` *pool* ... rather than a single forward host/port.

That stops being true here. Pools remain the default and the richer option, but
they are no longer mandatory, and the docstring must stop asserting otherwise.
This is a deliberate softening: requiring a pool to express one backend is
friction NPM users do not expect, and the capability is strictly additive.

## Backend — data model

### Migration `0014_host_forward_target`

Symmetric changes to **`proxy_hosts`** and **`proxy_host_locations`**:

| Column | Change |
|---|---|
| `upstream_id` | `NOT NULL` → nullable; keeps its RESTRICT foreign key |
| `forward_host` | new, `String(255)`, nullable |
| `forward_port` | new, `Integer`, nullable |

and on each table:

```sql
CHECK (forward_port IS NULL OR forward_port BETWEEN 1 AND 65535)

CHECK ((forward_host IS NOT NULL AND forward_port IS NOT NULL AND upstream_id IS NULL)
    OR (forward_host IS NULL     AND forward_port IS NULL     AND upstream_id IS NOT NULL))
```

`forward_scheme` already exists on both tables and needs no change: a single
host renders as `proxy_pass http://10.0.0.1:8080`, using the same column that
today prefixes the pool name.

Existing rows carry `upstream_id` with both new columns NULL and satisfy the
constraint unchanged, so **there is no data migration upward**.

**Downgrade is lossy, and more so than `0013`'s.** Restoring `NOT NULL` requires
deleting every host-targeted row, and for `proxy_hosts` that deletes *the vhost*,
not a detail of one — someone downgrading loses proxy hosts, and the sites they
serve stop being served. The migration states this at the top of its docstring,
not only beside the `DELETE`.

Note for whoever writes it: `op.drop_constraint` takes the **bare** constraint
name. The metadata naming convention is applied on top, so passing the expanded
`ck_proxy_hosts_...` form produces `ck_proxy_hosts_ck_proxy_hosts_...` and fails.

## Backend — validation

The rule streams already use, applied to the proxy-host and location payloads:
**exactly one target**, where a `forward_host` without a `forward_port` is
explicitly *not* a target. Enforced by a pydantic `model_validator` so the API
answers 422 rather than letting the database raise a 500, and by the DB check
constraint so a caller bypassing the schema still cannot write a bad row.

`_assert_pools_usable` in `app/services/proxy_host.py` becomes conditional:
there is no pool to check when the target is a host. The existing context rule
(a pool must allow `http`) still applies whenever a pool *is* chosen, unchanged.

## Backend — nginx rendering

### Specs (`app/services/nginx/state.py`)

`ProxyHostSpec.upstream_id` and `LocationSpec.upstream_id` become
`int | None`, and both gain `forward_host: str | None` and
`forward_port: int | None`.

### Renderer (`app/services/nginx/renderer.py`)

One helper resolves either shape:

```python
def _target(spec) -> str:
    """The proxy_pass destination: a pool name, or a literal host:port."""
    if spec.upstream_id is not None:
        return pool_name(spec.upstream_id)
    return f"{spec.forward_host}:{spec.forward_port}"
```

The template needs almost nothing. The macro is already
`proxy_block(path, scheme, pool, modifier="")` and uses its third argument only
as `proxy_pass {{ scheme }}://{{ pool }}` — it never inspects it, so the change
is a rename of that parameter to `target` plus renaming `location_pools` to
`location_targets` and building it from `_target`. No new branching in the
template at all.

### Loader (`app/services/nginx/loader.py`)

The skip rule needs the most care in this change. Today a host whose pool is
missing, disabled or empty is dropped from the render, because a `server` block
naming a non-existent `upstream` fails `nginx -t` and rolls back the whole apply.
That check must now apply **only when a pool is the target** — otherwise every
host-targeted host would be silently dropped from the config, which is the worst
possible failure for this feature: sites simply stop being served, with no error.

The same applies per-location. `http_upstreams` continues to collect only pools
an included host or location actually references; hosts that reference none
contribute nothing to it.

## Frontend

### Proxy host dialog (`components/proxy-hosts/proxy-host-dialog.tsx`)

The Forwarding tab's root row gains the Single host / Pool switch, defaulting to
Pool for a new host and to whichever the host uses when editing. Both modes'
values stay in form state so switching never discards typed input, and only the
active mode's values are submitted with the other side explicitly nulled — so
changing an existing host's target clears the old one rather than leaving both
set for the constraint to reject.

### Locations editor (`components/proxy-hosts/locations-editor.tsx`)

Each row gets its own independent mode with the same behaviour. This is the bulk
of the UI work: the editor currently renders one pool `Select` per row and now
renders either that or a host/port pair.

### List (`components/proxy-hosts/proxy-hosts-view.tsx`)

The **Upstream** column shows the pool name as now, or `host:port` for a
host-targeted row. `poolsById` stays — it is still needed to resolve names for
pool-targeted rows.

## Error handling

- The exactly-one-target rule answers 422 with a message naming the field; the
  dialog surfaces it through the existing `describeError` path.
- A host-targeted proxy host cannot fail for pool reasons: there is no pool to
  be missing, disabled, empty or wrongly-scoped. That removes a class of silent
  skip for those hosts, and is worth saying out loud because it is the main
  practical benefit beyond convenience.

## Testing

### Backend (pytest, Linux container)

- Both new check constraints, on both tables: a row with two targets and a row
  with none are rejected by the database.
- The schema validator, including the half-specified host (`forward_host` with
  no `forward_port`).
- Renderer: a host-targeted host emits `proxy_pass http://10.0.0.1:8080`; a
  pool-targeted one is byte-identical to today; a host with one location of each
  kind renders both correctly.
- Loader: a host-targeted host is **included** even though it references no pool
  — the regression this feature is most likely to introduce; a pool-targeted host
  with an empty pool is still skipped; `http_upstreams` omits pools nothing
  references.
- Migration `0014` up and down against real Postgres, asserting the downgrade
  deletes exactly the host-targeted rows and leaves pool-targeted ones intact.

### Frontend (vitest)

- Dialog: defaults to Pool for a new host, opens on the host's actual mode when
  editing, values survive a mode round-trip, payload carries exactly one target.
- Locations editor: per-row mode switches independently of its siblings.
- List: renders a pool name for one row and `host:port` for another.

## Phasing

1. **Root route.** `proxy_hosts` columns and constraints, `ProxyHostSpec`,
   `_target`, the loader's conditional skip, the dialog's root row, the list
   column. Locations stay pool-only and their editor offers no switch.
2. **Locations.** `proxy_host_locations` columns and constraints, `LocationSpec`,
   `location_targets`, and the per-row editor mode.

Phase 1 first: it establishes `_target`, the validator and the conditional skip
that phase 2 reuses. Doing locations first would build all three anyway, then
change them.
