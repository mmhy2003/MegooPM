# CrowdSec whitelists managed from the UI — design

## Goal

Let an operator define CrowdSec parser whitelists from the Security page so that
false-positive detections against internal backends stop producing bans, render
them into a YAML file CrowdSec reads, and reload CrowdSec so the change takes
effect without a manual deploy.

## Non-goals

- **Expression whitelists.** CrowdSec supports `expression:` whitelists matching
  arbitrary event fields. This design covers `ip:` and `cidr:` only, which is
  what the false-positive-by-source-address case needs. Expressions are a later
  schema addition, not a redesign.
- **Centralized allowlists** (`cscli allowlists`). Managed by a CLI on the LAPI
  host, and their HTTP API is the cloud console (`admin.api.crowdsec.net`),
  which this deployment disables via `DISABLE_ONLINE_API: "true"`.
- **Per-node whitelists.** CrowdSec is a single shared control-plane service, so
  a whitelist is cluster-wide by construction.
- **Bouncer-side allowlisting** in `infra/nginx/lua/megoopm_crowdsec.lua` —
  considered and rejected; see below.

## Decisions taken during brainstorming

**Whitelist at the CrowdSec layer, not the bouncer layer.** A bouncer-side
allowlist would need no restart and no privilege, but CrowdSec would still raise
the alert and still write the decision, so the Security page would show bans
that are not being enforced. The operator chose true whitelisting: the event is
dropped in the parser pipeline, so no alert and no decision exist.

**Reload by restarting the CrowdSec container over the docker socket.** Parser
whitelists are read at startup; CrowdSec exposes no reload endpoint and no LAPI
route for parser configuration. The socket is the only channel available.

**Two costs were raised before this was chosen, and accepted:**

1. *A restart briefly denies traffic on every protected host.* `APPSEC_URL` is
   required in `docker-compose.ha.yml`, so AppSec is on globally, and
   `infra/nginx/crowdsec-bouncer.conf` sets `APPSEC_FAILURE_ACTION=deny`. While
   CrowdSec is down, AppSec is unreachable and the bouncer fails closed. The
   window is a few seconds; the mitigation is that this design makes it
   impossible for a *bad* whitelist to turn that window into an open-ended
   outage — see **Validation and rollback**.
2. *The docker socket is a privilege escalation.* It is mounted into the
   **worker** only, never the API process: the worker takes no internet traffic,
   and it is where the reload task runs regardless.

## Backend — data model

### Migration `0016_crowdsec_whitelists`

`crowdsec_whitelist` — one row per whitelist document:

| column | type | notes |
| --- | --- | --- |
| `id` | int PK | |
| `name` | text, unique, not null | becomes the CrowdSec `name:` (namespaced on render) |
| `reason` | text, not null | CrowdSec `whitelist.reason`, appears in its logs |
| `description` | text, nullable | operator note, rendered as `description:` |
| `ips` | `ARRAY(Text)`, not null, server default `{}` | |
| `cidrs` | `ARRAY(Text)`, not null, server default `{}` | |
| `enabled` | bool, not null, default true | disabled rows are not rendered |
| `created_at` / `updated_at` | timestamptz | project convention |

Check constraint `ck_crowdsec_whitelist_not_empty`:
`cardinality(ips) + cardinality(cidrs) > 0`. A whitelist matching nothing is
always an operator mistake, and silently rendering an empty `whitelist:` block
is how it would go unnoticed. Note `op.drop_constraint` takes the bare name.

`crowdsec_whitelist_apply` — a single-row state table (`id` PK, always `1`),
mirroring how `cluster_state` records convergence:

| column | type | notes |
| --- | --- | --- |
| `id` | int PK | always `1` |
| `applied_digest` | text, nullable | sha256 of the file content last applied |
| `applied_at` | timestamptz, nullable | |
| `ok` | bool, not null, default true | false when the last apply failed |
| `error` | text, nullable | operator-facing failure text |

This table exists because the apply is asynchronous and can fail *after* the API
has returned 200. Without it a failed reload is invisible, and the UI would
imply a whitelist is active when it is not — the same silent-failure shape as
the LAPI timeout that stringified to `""`.

`ARRAY(Text)` is Postgres-only. Tests touching these tables must be
Postgres-gated, as with `proxy_hosts.domain_names`: the `@compiles` SQLite shim
fixes DDL only, and the bind still fails with `type 'list' is not supported`.

## Backend — rendering

New module `app/services/crowdsec/whitelists.py`, template
`app/templates/crowdsec/whitelist.yaml.j2`.

All enabled rows render into **one multi-document YAML file**. CrowdSec treats
`---`-separated documents in a single parser file as separate nodes — the same
convention the hub uses for multi-datasource acquisition files. One document per
row:

```yaml
# Managed by MegooPM — do not edit by hand.
name: megoopm/wl-internal-backends
description: "Internal backend pool"
whitelist:
  reason: "internal backends trip appsec generic rules"
  ip:
    - "10.10.0.14"
  cidr:
    - "10.10.0.0/24"
```

Rules:

- `name:` renders as `megoopm/wl-<slug>`. CrowdSec requires uniqueness across
  all loaded parsers; the `megoopm/` prefix guarantees no hub collision.
- Rendering is deterministic — rows ordered by `id` — so an unchanged set
  produces byte-identical output. The digest of that output decides whether a
  reload is needed at all, which keeps a no-op save from restarting CrowdSec.
  This mirrors the nginx renderer's byte-stability requirement and the
  certificate-fingerprint fix.
- With zero enabled rows the file renders as a comment-only placeholder and is
  never deleted: the path is a bind-mount source.

### Writing the file

Target `/data/crowdsec/whitelists/megoopm.yaml` (setting
`CROWDSEC_WHITELIST_PATH`), on the shared volume backend and worker already
mount.

**The file MUST be written in place — truncate and write, never
write-temp-then-rename.** The container sees this path through a single-file
bind mount, which resolves to an inode at container start. A rename swaps the
inode and the container keeps reading the old content forever, with no error in
any log. This is the opposite of the atomic-write habit correct everywhere else
in this codebase, so the writer carries a comment saying why.

## Infrastructure — mount and boot

On the `crowdsec` service in `docker-compose.ha.yml`:

```yaml
- ${SHARED_DATA_PATH:?}/crowdsec/whitelists/megoopm.yaml:/etc/crowdsec/parsers/s02-enrich/99-megoopm-whitelist.yaml:ro
```

A **file** mount, not a directory. `/etc/crowdsec/parsers/s02-enrich/` already
holds hub-installed parsers (geoip enrichment among them); bind-mounting a
directory over it would mask them and silently break enrichment.

`data-init` gains the seed, extending its existing inline command:

```
mkdir -p /data/crowdsec/whitelists
[ -f /data/crowdsec/whitelists/megoopm.yaml ] || printf '# Managed by MegooPM — no whitelists defined.\n' > /data/crowdsec/whitelists/megoopm.yaml
```

If the source path does not exist when the CrowdSec container starts, Docker
creates a **directory** there; CrowdSec then fails to parse it and refuses to
start, and with AppSec fail-closed that is a full outage on first boot. This
seed is what makes "load on boot" work. `data-init` already gates the other
services with `service_completed_successfully`.

`data-init` runs on every node, but the file lives on the shared mount, so the
`[ -f ]` guard makes the seed idempotent.

## Backend — reload path

New task module `app/tasks/crowdsec.py`, task `apply_crowdsec_whitelists`.

**Routing.** The CrowdSec container runs only on the control-plane node
(`profiles: ["control-plane"]`), but workers run on every node, and nothing in
`app/core/config.py` currently identifies that node. A new setting
`CROWDSEC_CONTROL_NODE_ID` names the node whose worker holds the socket; the API
enqueues onto `node_queue(that_id)` via the existing helper. When it is unset
the API saves the row and reports "reload is not configured on this cluster"
rather than enqueueing a task no one will run.

**Docker access** over the unix socket with `httpx`, already a dependency:

```python
transport = httpx.HTTPTransport(uds=settings.docker_socket_path)
```

then `POST /containers/{CROWDSEC_CONTAINER_NAME}/restart?t=10`. No docker SDK is
added. Compose mounts `/var/run/docker.sock:/var/run/docker.sock:ro` on the
**worker service only**.

**Task steps:**

1. Read the current file bytes and keep them in memory.
2. Render new content from the DB; compute its sha256.
3. If the digest equals `crowdsec_whitelist_apply.applied_digest`, record
   success and return — no restart.
4. Validate (below). On failure record `ok=false` with the message and return
   **without writing**.
5. Write in place.
6. Restart the container.
7. Poll `CrowdSecClient.ping()` until it succeeds or
   `CROWDSEC_RELOAD_HEALTH_TIMEOUT_SECONDS` (default 60) elapses.
8. On success record `ok=true`, the digest and `applied_at`. On timeout **write
   the kept bytes back in place, restart again**, and record `ok=false` with the
   error.

## Backend — validation and rollback

Validation runs before the file is written, in the task and again in the API so
the operator sees the error synchronously on save:

- every `ips` entry parses as `ipaddress.ip_address`;
- every `cidrs` entry parses as `ipaddress.ip_network(strict=False)`;
- `name` slugifies to a non-empty `[a-z0-9-]+` and is unique after slugification;
- the rendered document parses via `yaml.safe_load_all`, and each document has
  `name`, `description` and a `whitelist` mapping.

Rollback exists because the failure mode is severe and asymmetric: a malformed
parser file stops CrowdSec from starting at all, and with
`APPSEC_FAILURE_ACTION=deny` every protected host then denies every request
until a human intervenes. Step 8 bounds that to the health-check timeout.
Rollback is part of the feature, not a refinement.

## API

Extends `app/api/routes/crowdsec.py`, admin-only like the manual-decision route:

| method | path | purpose |
| --- | --- | --- |
| GET | `/crowdsec/whitelists` | list |
| POST | `/crowdsec/whitelists` | create, then enqueue apply |
| PATCH | `/crowdsec/whitelists/{id}` | update (including `enabled`), then enqueue |
| DELETE | `/crowdsec/whitelists/{id}` | delete, then enqueue |
| GET | `/crowdsec/whitelists/status` | the `crowdsec_whitelist_apply` row |
| POST | `/crowdsec/whitelists/apply` | re-enqueue apply (retry after a failure) |

Schemas in `app/schemas/crowdsec_whitelist.py`. Both regenerations are required:
`python -m scripts.export_openapi`, then `cd frontend && npm run gen:api`.

## Frontend

A fourth tab on the Security page — Dashboard / Active decisions / Recent alerts
/ **Whitelists** — following the tab pattern already there.

- `components/security/whitelists-table.tsx` — name, reason, IP/CIDR counts,
  enabled toggle (reusing `EnabledToggle`), edit and delete.
- `components/security/whitelist-dialog.tsx` — name, reason, description, and
  two textareas (one IP per line, one CIDR per line) with parse errors inline.
- The dialog shows a **read-only preview of the exact YAML** that will be
  rendered. The operator asked to author YAML; this shows them the artifact
  while making an unparseable one impossible.
- An apply-status banner above the table fed by `/crowdsec/whitelists/status`,
  showing `error` when `ok` is false with a Retry button hitting `/apply`. This
  is the surface that keeps a failed reload from being invisible.

## Error handling

| failure | behaviour |
| --- | --- |
| Invalid IP/CIDR | 422 from the API before anything is written |
| Duplicate name (after slugification) | 409 |
| `CROWDSEC_CONTROL_NODE_ID` unset | row saved; status reports "reload not configured"; no task enqueued |
| Docker socket absent or permission denied | status `ok=false` naming the socket path and errno |
| Wrong container name | status `ok=false` naming the container it tried |
| CrowdSec does not return healthy | previous file restored, container restarted again, status `ok=false` |
| Worker on the control node is down | task queues until it returns; status stays at the previous digest |

Every message names the file, container or setting involved. `CrowdSecClient._where()`
exists because an error with no message sent an operator chasing DNS and
firewalls for an afternoon; the same standard applies here.

## Testing

### Backend (pytest, in the Linux container)

- Renderer: one row to expected YAML; three rows to three `---` documents in
  `id` order; zero enabled rows to the placeholder comment; byte-stability
  across two calls with unchanged input.
- Slugification: name with spaces and uppercase to `megoopm/wl-...`; two names
  slugifying identically produce 409.
- Validation: bad IP, bad CIDR, empty `ips` and `cidrs` (constraint), name that
  slugifies to empty.
- Digest short-circuit: applying twice with no change performs no restart
  (assert the docker call is not made).
- Rollback: a stubbed docker transport plus a `ping` that never succeeds leaves
  the original bytes on disk and `ok=false` carrying the error.
- **In-place write:** capture the file's inode before and after a write and
  assert it is unchanged. This is the trap that would silently disable the whole
  feature, so it gets an explicit regression test.
- Table tests are Postgres-gated (`ARRAY`).

### Frontend (vitest)

- Table renders rows; the toggle calls PATCH with `enabled`.
- Dialog rejects a malformed IP without calling the API.
- YAML preview updates as fields change.
- Status banner renders `error` and Retry when `ok` is false, and renders
  nothing when `ok` is true.
- Tab test: Whitelists is the fourth tab and Dashboard remains the default.

Run `npx tsc --noEmit` separately — vitest does not typecheck.

## Phasing

1. Migration, model, schemas, renderer, validation (no wiring). Testable alone.
2. Compose mount and `data-init` seed. Verified by starting the stack and
   confirming CrowdSec loads the placeholder.
3. Reload task, docker transport, status table, rollback.
4. API routes and both OpenAPI regenerations.
5. Frontend tab, table, dialog, preview, status banner.

Phase 2 is the one that can break a running cluster. It lands before any UI can
write a non-empty file, so the placeholder path is proven first.
