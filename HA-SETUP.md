# MegooPM — multi-node (HA) setup guide

This is the step-by-step operator guide for running MegooPM on several hosts
with the **current** implementation (`docker-compose.ha.yml`). It is
deliberately practical; the design rationale (locking, propagation, failure
model) lives in [`docs/ha.md`](docs/ha.md).

> **Read §8 "Known limitation" before relying on this in production.** Today
> only the node that applies a change reloads its own nginx; the other nodes
> need the documented workaround until the reconcile fan-out is fixed.

---

## 1. Topology

```
                     ┌─────────────────────────────┐
     clients ───────▶│  Load balancer (yours)       │  :80/:443 TCP passthrough → node nginx
                     │  optionally :3000/:8000 too  │  :3000 UI / :8000 API (L7)
                     └──────┬──────────────┬────────┘
                            │              │
              ┌─────────────┴──┐     ┌─────┴──────────┐
              │ data-plane A   │     │ data-plane B   │   … N nodes, identical:
              │ nginx + agent  │     │ nginx + agent  │   docker-compose.ha.yml
              │ API, worker    │     │ API, worker    │   no profiles
              │ web UI         │     │ web UI         │
              └───────┬────────┘     └───────┬────────┘
                      │  /data (bind mount of SHARED_DATA_PATH)
                      ▼                      ▼
              ┌─────────────────────────────────────────┐
              │ SHARED STORAGE (NFS export)             │  rendered vhosts, certs,
              │ mounted at the same host path everywhere│  ACME account, htpasswd
              └─────────────────────────────────────────┘
              ┌─────────────────────────────────────────┐
              │ CONTROL PLANE (one host)                │  Postgres, Redis,
              │ docker-compose.ha.yml with              │  CrowdSec LAPI/AppSec,
              │ COMPOSE_PROFILES=control-plane,scheduler│  Celery beat
              └─────────────────────────────────────────┘
```

| Role | What runs there | How many |
| --- | --- | --- |
| **Control plane** | Postgres, Redis, CrowdSec LAPI + AppSec, Celery `beat` | exactly one (or replace Postgres/Redis with managed HA services) |
| **Data plane** | managed nginx (+ reload agent), API, Celery worker, web UI | as many as you like |
| **Shared storage** | NFS (or any shared filesystem) mounted on every data-plane node | one export |
| **Load balancer** | anything L4 for `:80/:443`; optionally L7 for `:3000/:8000` | yours — not part of the stack |

The control-plane host can *also* be a data-plane node (the same compose file
runs both sets of services), which is the simplest 2–3 node setup. A dedicated
control-plane host is cleaner: a data-plane failure then never takes the shared
services with it.

---

## 2. Prerequisites

- Linux hosts with Docker Engine + the Compose plugin (`docker compose version`).
- The MegooPM repository checked out **at the same commit on every node** — the
  compose file *builds* the images locally, so each node needs the source.
- An NFS server (or other shared filesystem) reachable from every data-plane node.
- Network reachability: every data-plane node → control-plane host on
  `5432` (Postgres), `6379` (Redis), `8080` (CrowdSec LAPI), `7422` (AppSec);
  the LB → every node on `80`, `443` (and `3000`, `8000` if balanced).
  Keep these on a private network — `CONTROL_PLANE_BIND` lets you pin the
  published control-plane ports to one interface.
- Four secrets, generated once and used **identically on every node**:

  ```bash
  openssl rand -hex 32   # SECRET_KEY        (encrypts DB-stored credentials — changing it later breaks them)
  openssl rand -hex 32   # CROWDSEC_BOUNCER_KEY
  openssl rand -hex 32   # CROWDSEC_REGISTRATION_TOKEN  (LAPI auto-validates the backend's machine; >= 32 chars)
  openssl rand -hex 32   # NGINX_RELOAD_TOKEN
  ```

- Two public URLs for the admin surface, as the browser will reach them through
  the LB: the UI origin (`CORS_ORIGINS`) and the API base
  (`NEXT_PUBLIC_API_BASE_URL`).

---

## 3. Shared storage

On the NFS **server**, export a directory for MegooPM. The backend/worker in
the containers run as **uid 1000** and must be able to write; nginx reads as
root. Either of these works:

```
# /etc/exports — pick ONE
/export/megoopm  10.0.0.0/24(rw,sync,no_subtree_check,no_root_squash)
/export/megoopm  10.0.0.0/24(rw,sync,no_subtree_check,root_squash,anonuid=1000,anongid=1000)
```

Plain `root_squash` without `anonuid` does **not** work: the one-shot
`data-init` service `chown`s `/data` to `1000:1000` at every start and fails
fast if it cannot.

On every **data-plane node**, mount the export at the same path
(`/mnt/megoopm` below) with NFS ≥ 4.1 — needed for close-to-open consistency
after a node's atomic rename:

```
# /etc/fstab
10.0.0.10:/export/megoopm  /mnt/megoopm  nfs  nfsvers=4.1,hard,noatime,_netdev  0  0
```

```bash
sudo mkdir -p /mnt/megoopm && sudo mount /mnt/megoopm
```

Never use `nolock`. Do not put `/var/run/megoopm` (the node-local run dir) on
the share — the containers keep it on their own filesystem by design.

---

## 4. Control-plane host

```bash
git clone <repo> megoopm && cd megoopm
cp .env.ha.example .env
```

Edit `.env`:

```dotenv
NODE_ID=node-cp
SHARED_DATA_PATH=/mnt/megoopm            # this host also mounts the share
COMPOSE_PROFILES=control-plane,scheduler # Postgres, Redis, CrowdSec, beat live here
RUN_MIGRATIONS=1                         # this node applies schema migrations
CONTROL_PLANE_BIND=10.0.0.10             # private interface the shared services are published on

# shared services — on THIS host they are the compose service names
DATABASE_URL=postgresql+asyncpg://megoopm:<db-password>@db:5432/megoopm
REDIS_URL=redis://redis:6379/0
CROWDSEC_LAPI_URL=http://crowdsec:8080
CROWDSEC_APPSEC_URL=http://crowdsec:7422
POSTGRES_PASSWORD=<db-password>          # must match DATABASE_URL

SECRET_KEY=<secret 1>
CROWDSEC_BOUNCER_KEY=<secret 2>
CROWDSEC_REGISTRATION_TOKEN=<secret 3>   # LAPI reads it too: the backend's machine is validated automatically
NGINX_RELOAD_TOKEN=<secret 4>

NEXT_PUBLIC_API_BASE_URL=https://megoopm-api.example.com
CORS_ORIGINS=https://megoopm.example.com
FIRST_ADMIN_EMAIL=admin@example.com      # seeded only while the users table is empty
FIRST_ADMIN_PASSWORD=<initial password>
```

CrowdSec needs no manual steps: the backend registers its LAPI machine itself
(`POST /v1/watchers` with the registration token) and LAPI validates it on the
spot; the bouncer is registered from `CROWDSEC_BOUNCER_KEY` at LAPI start. The
machine credentials live in the shared database, so every node shares one
machine — a new node needs nothing beyond the identical secrets.

Start it:

```bash
docker compose -f docker-compose.ha.yml up -d --build      # or: make ha-up
docker compose -f docker-compose.ha.yml ps                  # everything healthy?
```

This host now runs the shared services **and** a full data-plane node
(nginx, API, worker, UI). If you want a pure control-plane host, that is not a
supported profile combination today — run it as a node and simply don't send it
proxy traffic from the LB.

**Using managed Postgres / Redis instead:** drop `control-plane` from
`COMPOSE_PROFILES`, point `DATABASE_URL` / `REDIS_URL` at the managed
endpoints, and run CrowdSec LAPI wherever you like (its URL is all the nodes
need). Keep `scheduler` on exactly one node.

---

## 5. Data-plane nodes (repeat per node)

```bash
git clone <repo> megoopm && cd megoopm      # same commit as the control plane
cp .env.ha.example .env
```

Edit `.env` — only the first block differs from the control-plane host:

```dotenv
NODE_ID=node-a                            # unique per node
SHARED_DATA_PATH=/mnt/megoopm
COMPOSE_PROFILES=                         # no shared services here
RUN_MIGRATIONS=0                          # the control-plane node owns migrations

# shared services — the control-plane host's private address
DATABASE_URL=postgresql+asyncpg://megoopm:<db-password>@10.0.0.10:5432/megoopm
REDIS_URL=redis://10.0.0.10:6379/0
CROWDSEC_LAPI_URL=http://10.0.0.10:8080
CROWDSEC_APPSEC_URL=http://10.0.0.10:7422

SECRET_KEY=<secret 1>                     # IDENTICAL to the control plane
CROWDSEC_BOUNCER_KEY=<secret 2>
CROWDSEC_REGISTRATION_TOKEN=<secret 3>
NGINX_RELOAD_TOKEN=<secret 4>
NEXT_PUBLIC_API_BASE_URL=https://megoopm-api.example.com
CORS_ORIGINS=https://megoopm.example.com
```

```bash
docker compose -f docker-compose.ha.yml up -d --build
docker compose -f docker-compose.ha.yml ps
```

Then register the node with your load balancer. Because everything durable is
on the share and in Postgres, a new node needs no data migration: on its first
reconcile it reloads once and serves the current config.

---

## 6. Load balancer

Anything that can do TCP passthrough on `:80`/`:443` works (cloud LB, HAProxy,
keepalived + HAProxy, Traefik). Passthrough matters: each node's nginx
terminates TLS with the shared certificates, so the LB never needs them.
[`infra/ha/haproxy.cfg`](infra/ha/haproxy.cfg) is a complete example for a
standalone HAProxy host — replace the node addresses. Balance `:3000` (UI) and
`:8000` (API) too if you want the admin surface highly available; they are
stateless (JWT auth), so any node can answer.

Point DNS for your proxied sites at the LB, and the admin hostnames
(`NEXT_PUBLIC_API_BASE_URL`, `CORS_ORIGINS`) at the LB's `:8000`/`:3000`
frontends (behind your own TLS).

---

## 7. Verify the cluster

On the control-plane host:

```bash
# shared services healthy, beat running
docker compose -f docker-compose.ha.yml ps

# create something through the API (or the UI), then:
docker compose -f docker-compose.ha.yml exec db psql -U megoopm -d megoopm \
  -At -c "select config_version, updated_by from cluster_state;"
#  -> the version and the NODE_ID that applied it
```

On any data-plane node:

```bash
# the shared render is visible on the host path
ls /mnt/megoopm/nginx/conf.d/

# this node's nginx accepts it, via the reload agent (no Docker socket)
docker compose -f docker-compose.ha.yml exec worker python -m scripts.nginx_remote test

# this node's last-applied version (node-local marker)
docker compose -f docker-compose.ha.yml exec worker cat /var/run/megoopm/nginx-config.version
```

A node whose marker lags `cluster_state.config_version` has not reloaded yet —
see §8.

---

## 8. Known limitation — the reconcile fan-out (read this)

Design: when a node applies a change it bumps `cluster_state.config_version`
and broadcasts `reconcile_local_nginx` to every worker; each worker compares
the shared version with its local marker and reloads its own nginx. Beat also
broadcasts a reconcile every `HA_RECONCILE_INTERVAL_SECONDS` (15 s) as a
backstop.

**Current state:** the Celery *Broadcast* queue over Redis is not delivering
(details and evidence in [`docs/ha.md`](docs/ha.md#4-config-propagation--reload-fan-out)).
The node that applied the change reloads itself correctly; **the other nodes
do not pick it up**. Until this is fixed:

- Either apply configuration changes and then trigger the reconcile on each
  other node by hand:

  ```bash
  docker compose -f docker-compose.ha.yml exec worker python -W ignore -c \
    "from app.tasks.nginx import reconcile_local_nginx; print(reconcile_local_nginx())"
  ```

  (idempotent — a current node answers `already current`; a lagging node
  validates and reloads and advances its marker),

- or run that command from a host cron on every data-plane node, e.g. every
  minute. It only reloads when the shared version is newer, so it is cheap.

The fix (per-`NODE_ID` task queues instead of Redis fanout) is tracked as a
follow-up to MEG-35.

---

## 9. Operations

**Upgrading.** Pull the same commit on every node. Bring the control-plane
node (`RUN_MIGRATIONS=1`) up first — its backend applies Alembic migrations —
then the others with `docker compose -f docker-compose.ha.yml up -d --build`.
Migrations are additive; older nodes keep working during the roll.

**Changing `NEXT_PUBLIC_*`.** They are baked into the UI image at build time:
edit `.env`, then `docker compose -f docker-compose.ha.yml build frontend && … up -d frontend`
on every node.

**Adding a node.** §5. **Removing a node.** Drain it at the LB, `docker compose
-f docker-compose.ha.yml down` — it holds no authoritative state. If it ran
`scheduler`, move that profile to another node.

**Backups.** Postgres (`pgdata` volume on the control-plane node, or your
managed service's backups) and the shared export (`/export/megoopm` — certs,
ACME account key, rendered configs). Redis holds only transient task state.

**Secrets.** `SECRET_KEY` encrypts DNS-provider and CrowdSec credentials in the
database — rotating it invalidates them; re-enter those credentials afterwards.
`NGINX_RELOAD_TOKEN` and `CROWDSEC_BOUNCER_KEY` can be rotated by changing
them everywhere and recreating the affected containers.

**Logs.** `docker compose -f docker-compose.ha.yml logs -f worker` shows every
apply/reconcile with its result dict (`valid`, `reloaded`, `rolled_back`);
`… logs -f nginx` shows the reload agent's requests; `… logs -f beat` (control
plane) shows the periodic sweeps.

---

## 10. `.env` reference (per node)

| Variable | Per node / identical | Meaning |
| --- | --- | --- |
| `NODE_ID` | per node | unique id stamped on `cluster_state.updated_by` |
| `SHARED_DATA_PATH` | per node (usually the same path) | host directory of the shared mount → `/data` in containers |
| `COMPOSE_PROFILES` | per node | `control-plane`, `scheduler`, both, or empty |
| `RUN_MIGRATIONS` | `1` on one node | that node's backend runs `alembic upgrade head` on start |
| `CONTROL_PLANE_BIND` | control plane | interface the shared services are published on |
| `DATABASE_URL`, `REDIS_URL` | identical (host differs only if `control-plane` is local) | shared Postgres / Redis |
| `CROWDSEC_LAPI_URL`, `CROWDSEC_APPSEC_URL` | identical (same caveat) | shared CrowdSec |
| `POSTGRES_USER/PASSWORD/DB` | control plane | what the local Postgres is initialised with |
| `SECRET_KEY`, `CROWDSEC_BOUNCER_KEY`, `CROWDSEC_REGISTRATION_TOKEN`, `NGINX_RELOAD_TOKEN` | **identical** | secrets (the registration token lets LAPI auto-validate the backend's machine) |
| `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_AUTH_ENABLED`, `CORS_ORIGINS` | identical | admin surface as the browser sees it |
| `FIRST_ADMIN_EMAIL/PASSWORD` | control plane, first start only | initial admin seed |
| `NGINX_HTTP_PORT`, `NGINX_HTTPS_PORT`, `FRONTEND_PORT`, `BACKEND_PORT` | per node | host ports |
| `POSTGRES_PORT`, `REDIS_PORT`, `CROWDSEC_LAPI_PORT`, `CROWDSEC_APPSEC_PORT` | control plane | published control-plane ports |
| `ACME_*` | identical | Let's Encrypt directory, account email, DNS-01 propagation timings |
| `HA_RECONCILE_INTERVAL_SECONDS` | identical | beat's backstop reconcile cadence |
