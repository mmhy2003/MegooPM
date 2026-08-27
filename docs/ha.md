# High Availability (HA) — multi-node deployment

MegooPM can run across N nodes behind a load balancer with all mutable state on
a shared mount, so any node can serve traffic and no single node is the source
of truth. This document covers the architecture, storage layout, locking and
propagation model, singleton jobs, how to add/remove a node, and failure
behavior.

HA is **opt-in**: set `HA_ENABLED=true`. With it off, MegooPM keeps its original
single-host behavior (local file lock, no DB coordination) and none of this
applies.

---

## 1. Architecture

```
                       ┌──────────────────────────────┐
        clients ──────▶│      Load Balancer (LB)       │
                       │  :80/:443 → nginx data plane  │
                       │  :8000    → API nodes         │
                       └───────┬───────────────┬───────┘
                               │               │
        ┌──────────────────────┴───┐   ┌───────┴──────────────────────┐
        │  Node A                  │   │  Node B                       │
        │  ┌────────┐  ┌────────┐  │   │  ┌────────┐  ┌────────┐       │
        │  │FastAPI │  │ worker │  │   │  │FastAPI │  │ worker │       │
        │  └────────┘  └───┬────┘  │   │  └────────┘  └───┬────┘       │
        │  ┌────────┐      │reload │   │  ┌────────┐      │reload      │
        │  │ nginx  │◀─────┘       │   │  │ nginx  │◀─────┘            │
        │  └───┬────┘              │   │  └───┬────┘                   │
        └──────┼──────────────────┘   └──────┼───────────────────────┘
               │        both nodes read/write the same bytes
               ▼                             ▼
        ┌──────────────────────────────────────────────────┐
        │        SHARED MOUNT (NFS / shared volume)          │
        │   /data/nginx/conf.d , conf.d/stream , htpasswd    │
        │   /data/certs (fullchain/privkey, ACME account)    │
        └──────────────────────────────────────────────────┘
        ┌───────────────────────┐   ┌───────────────────────┐
        │  Postgres (shared)     │   │  Redis (shared broker) │
        │  cluster_state.version │   │  Celery + broadcast    │
        │  advisory locks        │   │  reconcile queue       │
        └───────────────────────┘   └───────────────────────┘
```

**Stateless app nodes.** FastAPI and Celery hold no local-only state. The only
node-local files are a *run directory* (`HA_LOCK_DIR`, default `/var/run/megoopm`)
holding fallback lock files and the **reload marker** — a tiny file recording the
config version this node last reloaded nginx for. Everything durable lives on the
shared mount or in Postgres/Redis.

---

## 2. Shared-storage layout

One shared root, `SHARED_DATA_DIR` (default `/data`), mounted at the same path on
every node. All state paths default to subdirectories of it and are individually
overridable:

| Path (env)                 | Default under `/data`             | Holds                                   |
| -------------------------- | --------------------------------- | --------------------------------------- |
| `SHARED_DATA_PATH` (host, compose only) | e.g. `/mnt/megoopm`  | the host directory bind-mounted to `/data` in every container |
| `NGINX_CONFD_DIR`          | `/data/nginx/conf.d`              | rendered `megoopm-*.conf`, `.htpasswd`  |
| `NGINX_STREAM_DIR`         | `/data/nginx/conf.d/stream`       | TCP/UDP `stream{}` forwards             |
| `NGINX_CERTS_DIR`          | `/data/certs`                     | `‹id›/fullchain.pem`, `privkey.pem`     |
| ACME account key           | `/data/certs/_acme/`              | shared ACME account key(s)              |
| `ACME_HTTP_CHALLENGE_DIR`  | `/data/certs/_acme-challenge`     | HTTP-01 challenge tokens                |

Because certs and the ACME account key live on the shared mount, **any** node can
issue, renew, and serve TLS. htpasswd sidecars are written into `conf.d` with the
`megoopm-` prefix, so they ride the same volume and the same atomic-write path.

> **Node-local, never shared:** `HA_LOCK_DIR` and `NGINX_RELOAD_MARKER_PATH`. The
> marker answers "has *this* node caught up?", so it must be per-node. Putting it
> on the shared mount would make every node think it is already current.

CrowdSec keeps its own local data in the CrowdSec container's volume; in HA it is
deployed as a shared/central LAPI that all nodes' bouncers talk to (see §6).

---

## 3. Locking model

Every mutation of the shared config set is serialized by a **cross-node lock**.
We use **Postgres advisory locks** rather than an NFS lock file, because Postgres
is already a hard shared dependency for every node and its lock semantics don't
depend on NFS mount options.

- **Apply lock** — `pg_advisory_xact_lock(APPLY_LOCK_KEY)`, held around the whole
  `render → nginx -t → reload → version-bump` sequence
  (`app/services/cluster/locks.py::apply_lock`). It is **transaction-scoped**: the
  version bump happens in the *same* transaction, so the lock releases and the new
  version becomes visible atomically. Two nodes applying at once are strictly
  serialized — neither can half-write `conf.d`. The engine's existing atomic-write
  + `nginx -t` + rollback semantics are preserved unchanged; the cross-node lock
  simply replaces the single-host `flock`.
- **Leader lock** — `pg_try_advisory_lock` (non-blocking), used to make periodic
  sweeps run once cluster-wide (§5).

On a non-Postgres engine (the SQLite test engine, or a single host without a
shared DB) both locks fall back to an OS `flock` file, which is correct within one
host and keeps the code path exercisable without Postgres.

---

## 4. Config propagation / reload fan-out

**Chosen mechanism: a shared `config_version` row in Postgres + per-node
reconcile, with a Celery broadcast as the fast path.** Rationale and the rejected
alternative are in the MEG-35 thread; summary:

1. The writing node applies the change (files land on the shared `conf.d`) and
   **bumps** `cluster_state.config_version` inside the apply transaction.
2. It records the new version in its own reload marker (it already reloaded
   in-place) and fans a `reconcile_local_nginx` task out to **every** node over a
   Celery **Broadcast** queue (`megoopm_reconcile`). A Broadcast queue delivers
   one copy per worker, so every node's worker receives it.
3. Each node's `reconcile_local_nginx` compares the shared version to its local
   marker. If the shared version is newer, it reloads its *local* nginx (the files
   are already on the shared mount) and advances its marker. Idempotent: an
   already-current node does nothing.
4. **Self-healing backstop:** beat also emits a reconcile every
   `HA_RECONCILE_INTERVAL_SECONDS` (default 15s in the reference stack). A node
   that missed the broadcast (was down / partitioned) catches up on the next tick.

Why the DB version is the source of truth and not raw Redis pub/sub: pub/sub has
no delivery guarantee and no catch-up for a node that was offline. The version row
+ shared files + idempotent apply mean a lagging node always converges.

> **Known issue (2026-08-28):** with the Redis broker the `megoopm_reconcile`
> Broadcast queue is not delivering. Verified on a single `docker-compose.ha.yml`
> node: `beat` logs `Sending due task reconcile-nginx-across-nodes` every 15 s and
> the apply path calls `reconcile_local_nginx.delay()`, but the worker — which
> declares `bcast.<uuid>` bound to the `megoopm_reconcile` fanout exchange — never
> logs a `received` for it (and `redis-cli PUBSUB CHANNELS` shows no subscriber).
> The applying node still reloads itself inside `apply_config`, so a one-node
> cluster is correct; **other nodes do not reload until this is fixed.** The
> likely fix is to stop relying on Redis fanout: give each worker a direct queue
> named after `NODE_ID` and have the apply path enqueue one reconcile per
> registered node (or have beat's periodic reconcile target every node queue).

```
 node A: write ──▶ [apply-lock] render+reload+bump(v→N) ──▶ broadcast reconcile
                                                              │
 node B: reconcile ── shared v=N > local v=N-1 ? ──▶ reload local nginx, mark N
```

---

## 5. Singleton periodic jobs

Cert renewal (and any future CrowdSec sync) must run **once cluster-wide**, not
once per node. Each such sweep grabs a non-blocking **leader lock**
(`leader_lock(engine, "cert-renew-sweep")`): whichever node wins does the work,
the rest no-op. This makes it safe to run more than one `beat` (e.g. transiently
during a rolling deploy) without double-enqueuing renewals. Run a single `beat`
in steady state anyway; the leader lock is the correctness guarantee, not a
license to run many.

---

## 6. Reference deployment (per-node compose)

`docker-compose.ha.yml` is run **on every node** with that node's `.env`
(template: `.env.ha.example`):

```bash
cp .env.ha.example .env      # set NODE_ID, SHARED_DATA_PATH, secrets, shared URLs
docker compose -f docker-compose.ha.yml up -d --build      # or: make ha-up
```

Every node runs the API, a worker, the managed nginx (with its reload agent)
and the web UI against `SHARED_DATA_PATH` (mounted at `/data`) and the shared
Postgres/Redis/CrowdSec. Profiles, chosen with `COMPOSE_PROFILES` in the
node's `.env`:

| Profile | Runs | Where |
| --- | --- | --- |
| `control-plane` | Postgres, Redis, CrowdSec LAPI/AppSec, published on `CONTROL_PLANE_BIND` | one node (small clusters) — otherwise point the `*_URL` variables at managed/external services |
| `scheduler` | the single Celery `beat` | exactly one node |

Set `RUN_MIGRATIONS=1` on the node you upgrade first and `0` elsewhere. There
is no load balancer in the stack: put yours in front of `:80`/`:443` (TCP
passthrough, so each nginx terminates TLS with the shared certs) and, if the
admin surface should be balanced, `:3000`/`:8000` — `infra/ha/haproxy.cfg` is
a complete example.

### Shared mount and uid 1000

The backend/worker run as uid 1000 and nginx reads as root; `data-init`
creates the layout and `chown`s `/data` to `1000:1000` on every start and
fails fast if it cannot. On NFS that means either `no_root_squash` on the
export or `root_squash` with `anonuid=1000,anongid=1000` (so squashed root
becomes the app user); with plain `root_squash` the chown fails and the node
will not start.

### NFS mount (production, multi-host)

Mount the export on every **host** (e.g. at `/mnt/megoopm` via `fstab`) and set
`SHARED_DATA_PATH=/mnt/megoopm`; compose bind-mounts it into the containers.

**Required NFS mount options:**

- **`nfsvers=4.1`** (or `4.2`) — needed for reliable byte-range/advisory locking
  and close-to-open consistency. Do **not** use `nolock`.
- **`hard`** — retry indefinitely on server hiccups rather than returning errors
  mid-write (protects atomic renames).
- **`noatime`** — avoids needless metadata writes on every read.

Even though nginx-apply mutual exclusion uses Postgres advisory locks (not NFS
locks), close-to-open consistency matters: after node A's atomic rename, node B
must observe the new file bytes on its next open. `nfsvers>=4.1` provides this.

External LB: any L4/L7 LB works (cloud LB, HAProxy, Traefik). Terminate or pass
through TLS at the nginx nodes; keep the LB cert-agnostic for `:443` passthrough,
or terminate at the LB and re-encrypt if you prefer.

---

## 7. Adding / removing a node

**Add a node:** mount the shared export at the same host path, copy
`.env.ha.example` to `.env` with a new `NODE_ID`, the shared `*_URL`s and the
identical secrets, no profiles, `RUN_MIGRATIONS=0`; run
`docker compose -f docker-compose.ha.yml up -d --build`; register it with the
LB. On first `reconcile` the new node's marker is absent (reads as `-1`), so it
reloads once and immediately serves the current config. No data migration, no
seeding.

**Remove a node:** drain it at the LB, then stop it. Because it holds no
authoritative state, nothing is lost. If it held the beat leader lock, the lock is
released on disconnect and the next sweep re-elects a leader.

---

## 8. Failure / failover behavior

| Failure                     | Behavior                                                                 |
| --------------------------- | ------------------------------------------------------------------------ |
| One app node dies           | LB routes to survivors; shared state intact; no config/cert loss.        |
| Node partitioned from DB    | Its apply/reconcile calls fail fast; other nodes unaffected; it catches up via the version row on rejoin. |
| Two nodes apply at once     | Serialized by the advisory lock; the config set is never half-written.   |
| A node misses the broadcast | The periodic reconcile backstop reloads it within `HA_RECONCILE_INTERVAL_SECONDS`. |
| Duplicate beat during deploy| Sweeps are leader-locked → run once; no double renewal.                  |
| nginx reload fails on a node| That node keeps its last-known-good config (engine rollback); other nodes still serve. |

Postgres and Redis are the remaining single points of truth. For full HA, run
them in their managed/clustered HA form (e.g. Patroni / a managed Postgres, Redis
Sentinel/Cluster); MegooPM only requires that they are reachable from all nodes.

---

## 9. Configuration reference

| Env                              | Default                               | Purpose                                              |
| -------------------------------- | ------------------------------------- | ---------------------------------------------------- |
| `HA_ENABLED`                     | `false`                               | Turn on cross-node coordination.                     |
| `SHARED_DATA_DIR`                | `/data`                               | Shared root for all mutable state.                   |
| `NGINX_CONFD_DIR` etc.           | derived from `SHARED_DATA_DIR`        | Individual state paths (override to relocate).       |
| `NODE_ID`                        | hostname                              | Node identifier stamped on version bumps.            |
| `HA_LOCK_DIR`                    | `/var/run/megoopm`                    | **Node-local** run dir for fallback locks.           |
| `NGINX_RELOAD_MARKER_PATH`       | `/var/run/megoopm/nginx-config.version` | **Node-local** last-applied-version marker.        |
| `HA_RECONCILE_INTERVAL_SECONDS`  | `30` (`15` in `docker-compose.ha.yml`) | Backstop reconcile cadence per node.                |
| `SHARED_DATA_PATH`               | —                                     | Compose only: host directory bind-mounted to `/data`. |
| `NGINX_RELOAD_TOKEN`             | —                                     | Shared secret between the worker and the nginx reload agent. |
| `NGINX_AGENT_ADDR`               | `nginx:9099`                          | Where the worker's `scripts.nginx_remote` finds its local nginx agent. |
| `COMPOSE_PROFILES`               | —                                     | Per-node roles: `control-plane`, `scheduler`.        |

See `backend/.env.example` for the annotated list.
