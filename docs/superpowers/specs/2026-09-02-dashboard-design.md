# The dashboard — design

## Goal

A dashboard page, first in the sidebar, showing the state of the instance at a
glance: certificate health, config/cluster health, security activity, live
traffic, and a globe of where attacks are coming from.

## Scope

This is **P1** of a four-part decomposition agreed during brainstorming:

| | | |
| --- | --- | --- |
| **P1** | Dashboard on data that already exists | this spec |
| P2 | Request analytics pipeline (all visitors) | not started |
| P3 | GeoIP for visitor traffic | not started |
| P4 | Realtime push, replacing polling | not started |

P1 is polled, not realtime. That is deliberate: the numbers here change on the
order of seconds at best, and P4 can push the same payload shape later without
rewriting a card.

## What already exists, and what does not

The dashboard is built only from data the instance already has:

- **Certificates** — `certificates.expires_on` and `.status`
  (`pending` / `active` / `failed` / `expired`).
- **Cluster** — `cluster_node.applied_version` and `.last_seen_at` against
  `cluster_state.config_version`. Drift is already computable; nothing surfaces
  it outside the cluster page.
- **Security** — the CrowdSec APIs already wired up (`list_decisions`,
  `list_alerts`).
- **Inventory** — proxy, redirection and dead hosts, streams.

Two things had **no source at all** and are added here:

- **Live connections and request rate.** Nothing enables nginx `stub_status`.
- **Nothing else.** In particular there is still no per-request data anywhere:
  access logs go to a file inside the nginx container and to CrowdSec's syslog,
  never to the database. That is P2's job, and it is why the globe in P1 shows
  attackers rather than visitors.

### The globe is a threat map, not a traffic map

Verified in the `crowdsecurity/crowdsec:v1.6.4` image already in use:
`parsers/s02-enrich/geoip-enrich.yaml` ships baked in, and so do
`GeoLite2-City.mmdb` and `GeoLite2-ASN.mmdb`. So CrowdSec resolves country, ASN
and coordinates for every alert it raises, and MegooPM's `AlertSource` schema
already models `ip`, `cn` and `as_name`.

That means the globe needs **no GeoIP subsystem, no MaxMind account and no new
data pipeline** — but it can only show IPs CrowdSec flagged. It answers "who is
attacking me", not "who is using my sites". The operator chose this knowingly,
with the traffic layer to follow in P2.

## Architecture

### One aggregate endpoint

`GET /api/v1/dashboard/summary` returns every card's numbers in one payload.

Composing the page from the existing list endpoints was rejected: the browser
would download every host and every certificate merely to count them, across
five round trips, getting slower as the instance grows. Counting belongs in
SQL.

It also gives P4 its seam — the same payload shape pushed over SSE, so no card
is rewritten when polling is replaced.

The endpoint is admin-only, like every other management route.

### Live traffic in a cluster

`NGINX_AGENT_ADDR: nginx:9099` means each backend reaches only its
**co-located** nginx, and in HA every node runs the full stack. A node can
measure only itself.

So this mirrors the pattern already in the codebase: **each node's beat scrapes
its own nginx and upserts a row keyed by `node_id`**, exactly as `cluster_node`
already records `applied_version` and `last_seen_at`. The dashboard aggregates
across rows.

```
node A beat ──scrape──> node A nginx :8081/stub_status ──upsert──┐
node B beat ──scrape──> node B nginx :8081/stub_status ──upsert──┤
                                                                  ▼
                                          node_metrics (shared database)
                                                                  │
                                    GET /dashboard/summary ────────┘
```

Consequences worth stating: no node ever reaches another node's nginx; a single
node works unchanged; and a node that stops reporting drops out of the totals
rather than freezing them, on the same staleness rule the cluster page uses.

`stub_status` is exposed on `listen 8081` inside the nginx container, with
**no `ports:` mapping**, so it is reachable as `nginx:8081` from the backend on
the compose network and not at all from outside the host. Binding it to
`127.0.0.1` would be tighter but would defeat the design: the scraper runs in a
*different* container, so a loopback-only listener could never be reached. This
is the same posture the reload agent on `:9099` already has — private by not
being published, not by being loopback-bound.

The status server answers only `/stub_status` and returns 404 for everything
else, so an attacker who reaches the compose network learns connection counts
and nothing more.

### Request rate is an average, not a live figure

`stub_status` reports cumulative counters, so a rate is the delta between two
scrapes divided by the elapsed time. The scrape runs on each node's existing
beat every **15 seconds**, so the figure is a 15-second average.

This is recorded because it sets an expectation the UI must not oversell: the
card shows a smoothed rate, and a one-second burst is invisible. Presenting it
as a live graph would be a lie about the data. If per-second resolution is ever
wanted, that is a different mechanism, not a smaller interval.

## The cards

| card | shows | source |
| --- | --- | --- |
| **Certificates** | expiring ≤30 days, expired, failed issuance | `certificates` |
| **Config health** | nodes in sync / drifted, current version, nodes gone quiet | the existing cluster-status service |
| **Security** | active bans, alerts in 24h, top scenarios | CrowdSec API |
| **Live traffic** | active connections, requests/sec | `node_metrics` |
| **Inventory** | hosts by type, enabled vs disabled, streams | host tables |

**Certificates is deliberately first.** An expiring certificate is the failure
that takes a site down silently and is invisible until it happens; every other
card describes something the operator can already see elsewhere.

**Config health is the MegooPM-specific one.** A node that has not applied the
current config is serving stale rules, and today nothing says so unless someone
opens the cluster page.

It **reuses the computation behind `GET /api/v1/cluster/status`**, which already
derives `in_sync`, `stale` and `converged` from `cluster_node` and
`cluster_state`. Recomputing it here would let the dashboard and the cluster
page disagree about whether the instance is converged, which is worse than
either being wrong alone.

## The globe

Input is a plain list of `{country, count, lat, lng}` — deliberately *not*
CrowdSec-shaped — so P2's traffic layer feeds the same component without
touching it. Countries are coloured by count.

**The library is chosen by measurement, not upfront.** Most 3D globes are
three.js-based and add several hundred kilobytes to a page that is otherwise
cheap. The implementation measures the bundle cost first; if it is
disproportionate, the fallback is a flat world map with a country choropleth,
which satisfies the same question ("where is this coming from") at a fraction
of the weight. Either way the component's props are the list above, so the
decision is reversible.

## Data flow

```
                   ┌── certificates ────┐
                   ├── cluster_* ───────┤
GET /dashboard/  ──┼── host tables ─────┼──> DashboardSummary ──> cards
    summary        ├── node_metrics ────┤
                   └── CrowdSec API ────┘

GET /dashboard/threats ──> CrowdSec alerts ──> [{country, count, lat, lng}] ──> globe
```

The globe is a **separate endpoint** from the summary: it is the only part that
depends on a network call to CrowdSec, it returns a list rather than scalars,
and it is the piece most likely to be slow or unavailable. Keeping it separate
means a CrowdSec outage empties the globe instead of blanking the whole page.

## Error handling

**A failing source degrades its own card, never the page.** CrowdSec being
unreachable must not stop certificate expiry from rendering — that inverts the
priority, since the cert card is the one that matters most. Each group in the
payload is independently nullable, and a card with no data says so rather than
showing a zero, because "0 active bans" and "CrowdSec is down" mean opposite
things.

**Stale node metrics are excluded, not shown as zero.** A node that stopped
reporting has unknown connections, not none. Staleness uses the existing
`settings.node_liveness_window_seconds` rather than a second threshold of its
own — two different definitions of "this node is gone" on one page would be a
bug waiting to happen.

## Testing

**Pure and heavily covered:** the `stub_status` parser (a fixed text format,
including a malformed body), the staleness rule for node metrics, the rate
calculation across two samples (including a counter reset when nginx restarts,
which must not produce a negative rate), and the drift computation.

**Against seeded rows:** each count, including the boundaries — a certificate
expiring in exactly 30 days, a node exactly at the staleness cutoff.

**Against a stubbed CrowdSec:** the threat list groups by country and survives
alerts with no `cn`, and an unreachable CrowdSec empties the globe without
failing the summary.

**Frontend:** each card from fixtures, including its empty and error states.

**Not automatable:** whether the globe looks right, and whether the numbers
match a live instance.

## Files

**Backend**

- `app/models/node_metrics.py` (new) — the per-node sample
- `alembic/versions/0022_node_metrics.py` (new)
- `app/services/dashboard.py` (new) — the counting, pure over a session
- `app/services/nginx/stub_status.py` (new) — the parser, pure
- `app/tasks/metrics.py` (new) — the per-node scrape, on the existing beat
- `app/api/routes/dashboard.py` (new) — the two endpoints
- `app/schemas/dashboard.py` (new)
- `infra/nginx/nginx.conf` — the `:8081` status server (unpublished)

**Frontend**

- `src/app/(app)/dashboard/page.tsx` (new)
- `src/components/dashboard/` (new) — one file per card, plus the globe
- `src/config/nav.ts` — the entry, first, before Proxy Hosts

## Non-goals

- **Realtime.** P4. The endpoint shape is chosen so it can be pushed later.
- **Per-visitor data.** P2. Nothing here stores an IP that CrowdSec did not
  already flag.
- **Historical charts.** Every number is current-state. Trends need retained
  samples, which is a storage and retention question of its own.
- **Per-host breakdowns.** The dashboard is instance-wide.

## Open risks

**`node_metrics` grows if it is written as history rather than upserted.** The
design stores one row per node, overwritten. Anything else needs a retention
policy, and this spec deliberately does not open that door.

**The CrowdSec alert list is not a complete picture of attacks.** It shows what
CrowdSec's installed collections detect. An empty globe means "nothing was
flagged", which an operator may read as "nothing happened". The card should
distinguish the two.

**A 15-second scrape interval on a busy instance adds a request to nginx per
node per interval.** Negligible, but it is a new periodic load and worth
knowing it exists.
