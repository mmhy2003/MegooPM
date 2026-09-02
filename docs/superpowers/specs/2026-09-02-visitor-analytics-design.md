# Visitor analytics — design

## Goal

Record which IP addresses and countries reach the managed hosts, so the
dashboard can list visitors and, later, draw real traffic on its map.

## Scope

This is **P2** of the dashboard decomposition:

| | | |
| --- | --- | --- |
| P1 | Dashboard on data that already exists | shipped |
| **P2** | Visitor analytics: ingestion, storage, retention | this spec |
| P3 | Traffic layer on the map | not started |
| P4 | Realtime push, replacing polling | not started |

P3 shrinks considerably as a result of this spec: the country lookup lands here,
so P3 becomes only the map layer that renders it.

## Why

Nothing stores per-request data. Access logs go to a file inside the nginx
container — lost when it is recreated — and optionally to CrowdSec's syslog,
which keeps only what its scenarios flag. So the dashboard can show *attackers*
(from CrowdSec alerts) but has no idea who its actual visitors are.

## The two decisions that shape everything

**Per-IP aggregates, not per-request rows.** At 100 req/s a proxy produces ~8.6
million rows a day. The operator chose one row per distinct IP instead, so the
table grows with *visitors* (thousands) rather than *requests* (millions). This
answers "which IPs and countries visited" directly, which was the ask.

**Bucketed by day.** One row per `(ip, day)`. Lifetime counters would make "who
visited in the last 24 hours" unanswerable; hourly buckets would be 24× the rows
for a question nothing currently asks. Daily bucketing also makes pruning a
single `DELETE WHERE day < cutoff`.

Size is distinct-IPs × retention-days: 20k daily visitors kept 30 days is ~600k
rows, which Postgres does not notice.

## Ingestion

### Verified before designing

The OpenResty image already carries `lua-resty-redis`
(`/usr/local/openresty/lualib/resty/redis.lua`) and `ngx_lua 0.10.26`, which
provides `log_by_lua`. Redis is already running for Celery. So this approach
adds **no new package, no new container and no new service**.

### The mechanism

`log_by_lua_block` runs on every request and performs two `HINCRBY`s:

```
megoopm:visits:count:2026-09-02   { "203.0.113.9": 412, ... }
megoopm:visits:bytes:2026-09-02   { "203.0.113.9": 88231, ... }
```

The IP is `ngx.var.remote_addr`, which the `real_ip` module has already
rewritten from `NGINX_REAL_IP_HEADER` where one is configured. So a request
behind a CDN is attributed to the actual client rather than to the proxy — the
same correction the CrowdSec bouncer already depends on. Reading the header
directly would be wrong: it is only trustworthy from the configured
`set_real_ip_from` ranges, and that judgement is what the module encodes.

Both keys are given a TTL comfortably longer than the flush interval, so a
permanently-down worker cannot grow Redis without bound.

### The hard rule

**Losing analytics must never cost a served request.** The Lua uses a short
connect/read timeout and wraps everything in `pcall`; any failure is swallowed
and the request completes normally.

The log phase runs *after* the response is sent, so there is no client-visible
latency — but a hung Redis could still occupy a worker for the duration of its
timeout, which is why the timeout matters and `pcall` alone is not enough.

### Approaches rejected

| approach | why not |
| --- | --- |
| second `access_log syslog:` into a MegooPM collector | needs a new long-running UDP service, and parses `combined` text back into fields nginx already had structured |
| tail the access log file | needs a shared volume, rotation handling and offset tracking across restarts — the most ways to silently lose or double-count |

## Flush

A Celery beat task every 60 seconds, wrapped in the existing
**`leader_lock`** from `app/services/cluster/locks.py`.

The lock is not optional. `docker-compose.ha.yml` requires a **shared** Redis
(`REDIS_URL is required (shared Redis)`), so every node sees the same counters.
Without the lock, three nodes would each drain and upsert the same numbers, and
the counts would be multiplied by the cluster size.

Draining is: read the day's hashes, upsert, then remove exactly the fields that
were read. Deleting the whole key would discard increments that arrived during
the flush.

**The upsert adds rather than replaces:**

```sql
ON CONFLICT (ip, day) DO UPDATE
SET request_count = visitor_day.request_count + EXCLUDED.request_count,
    bytes = visitor_day.bytes + EXCLUDED.bytes,
    last_seen_at = EXCLUDED.last_seen_at
```

Each flush carries the delta since the previous one, not a running total. A
replacing upsert would silently reset every visitor's count once a minute.

The arithmetic this buys: 8.6M requests become 8.6M Redis increments — trivial
for Redis — and roughly **20k Postgres upserts**, because the aggregation
happens where it is cheapest.

## Storage

One table, `visitor_day`:

| column | notes |
| --- | --- |
| `ip` | part of the primary key |
| `day` | date, part of the primary key |
| `first_seen_at`, `last_seen_at` | timestamps |
| `request_count`, `bytes` | summed across flushes and across nodes |
| `country` | ISO-3166 alpha-2, resolved at flush time |

No ASN column. The bundled database is country-only, so the column could never
be filled, and a column that is always null invites someone to "fix" it by
adding a dependency nobody asked for. An operator who supplies a City or ASN
database can have one added then, with a reason.

## Country resolution

Resolved **at flush time, once per distinct IP** rather than per request, from a
bundled **DB-IP IP-to-Country Lite** database in MMDB format.

DB-IP rather than MaxMind: GeoLite2 requires an account, a signed EULA and a
license key, and its terms restrict redistribution, so it cannot be baked into a
shipped image. DB-IP Lite is CC BY 4.0 with attribution and needs no account, so
the feature works on a fresh install. Both are MMDB, so the reader is the same
and an operator who wants MaxMind's accuracy can point the setting at their own
file — a config path, not a code branch.

**Verify the current licence terms before shipping.** That is a legal question
and this spec is not authority on it.

A lookup that fails leaves `country` null rather than failing the flush: an
unlocatable visitor is still a visitor.

## Retention

**30 days by default, configurable, pruned by a daily task.**

This is a requirement, not an option. The rows are visitor IP addresses, which
are personal data under GDPR; a table with no expiry is a liability that grows
quietly and is discovered at the worst possible moment. Daily bucketing makes
the prune a single `DELETE WHERE day < cutoff`.

## What this deliberately cannot answer

Per-request questions: *which URL* an IP requested, *when* within the day, or
*which host* it reached. The grain is `(ip, day)`, instance-wide.

This is stated plainly so it reads as a decision rather than an oversight. Any
of those needs a different table and a different volume conversation — per-host
breakdowns multiply the row count by the number of hosts a visitor touches.

## Files

**Infra**

- `infra/nginx/lua/megoopm_analytics.lua` (new) — the `log_by_lua` body
- `infra/nginx/nginx.conf` — load it, plus the Redis connection settings
- `infra/nginx/docker-entrypoint.sh` — pass `REDIS_URL` through to nginx

**Backend**

- `app/models/visitor_day.py` (new)
- `alembic/versions/0023_visitor_day.py` (new)
- `app/services/analytics/flush.py` (new) — drain Redis, upsert
- `app/services/analytics/geoip.py` (new) — the MMDB reader
- `app/tasks/analytics.py` (new) — the flush and prune tasks
- `app/core/celery_app.py` — schedule both
- `app/api/routes/dashboard.py` — a visitors endpoint
- `Dockerfile` — bundle the DB-IP database

**Frontend**

- a visitors panel on the dashboard

## Testing

**Pure, and where the risk is:** the Redis-hash-to-rows transformation, the
additive upsert (two flushes of the same IP sum rather than replace), the prune
cutoff at its boundary, and the GeoIP reader returning null for a private or
unknown address.

**Against Postgres:** the `(ip, day)` conflict target actually adds, and a
second node's flush sums rather than overwrites.

**Against real nginx:** a request produces the expected Redis increment, and —
the important one — **nginx still serves normally with Redis stopped**.

**Not automatable:** whether the counts match reality under real traffic.

## Open risks

**`log_by_lua` runs on every request.** Even with timeouts and `pcall`, this is
new work in the hot path of a reverse proxy. The mitigation is that it is two
Redis commands on an already-open pooled connection, and that it runs after the
response. It should still be the first thing suspected if latency regresses.

**A flush that crashes mid-drain loses that minute's counters** for the fields
it had already removed. Accepted: the data is approximate visitor analytics, not
billing, and the alternative — a two-phase handoff — is disproportionate.

**Distinct-IP growth is unbounded within a day.** An IPv6 scanner walking a /64
would create a very large number of distinct addresses in one day's hash. The
TTL and the daily prune bound the damage, but a deliberate attacker can inflate
the table. Worth watching before it is worth solving.
