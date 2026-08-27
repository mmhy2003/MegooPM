# nginx Config Generation & Reload Engine (MEG-16)

Turns database state into live nginx configuration: render → validate → reload,
with rollback on any failure. Config is a **pure, idempotent function of the
database** and reloads run **asynchronously via Celery** with an observable
result.

## Pipeline

```
DB (enabled proxy hosts + pools)
  └─ loader.load_desired_state ──► DesiredState (plain DTOs)
       └─ renderer.render_config ──► {filename: contents}   (pure, deterministic)
            └─ engine.apply_config ──► lock → diff → write → `nginx -t` → reload
                                        (rollback to last-known-good on failure)
```

| Layer | Module | Responsibility |
| --- | --- | --- |
| State | `app/services/nginx/state.py` | Immutable DTOs — the render input. |
| Load | `app/services/nginx/loader.py` | ORM → DTOs (async + sync wrapper). |
| Render | `app/services/nginx/renderer.py` + `app/templates/nginx/*.j2` | DTOs → `.conf` text. |
| Control | `app/services/nginx/controller.py` | `nginx -t` / `nginx -s reload` behind a Protocol. |
| Apply | `app/services/nginx/engine.py` | Locking, atomic writes, validation, rollback. |
| Task | `app/tasks/nginx.py` | Celery `reload_nginx_config` — the async seam. |
| API | `app/api/routes/nginx.py` | `POST /nginx/reload`, `GET /nginx/preview` (admin). |

## Generated files

One file per object, named by id so updates rewrite in place (never duplicate):

- `megoopm-upstream-{id}.conf` — an `upstream {}` block (load-balancing method,
  per-backend `weight` / `max_fails` / `fail_timeout` / `backup` / `down`).
- `megoopm-proxy-{id}.conf` — the `server {}` block(s): plain `:80`, or a `:443`
  TLS server (+ `:80` redirect when `ssl_forced`) when a certificate is set.
  Honours HSTS, HTTP/2, websocket upgrade, exploit blocking, asset caching and
  free-form `advanced_config`.
  Extra per-path routes (`proxy_host_locations`) render as `location ^~ <path>`
  blocks pointing at their own pool; `^~` makes the longest matching prefix win
  over the asset-caching regex location, so `/api/app.js` reaches the API pool.
  Host-wide options (websockets, forwarded headers, auth stripping) apply to
  every location; a location whose pool has no backends is omitted.

Only files beginning with `NGINX_MANAGED_PREFIX` (default `megoopm-`) are managed;
hand-placed configs in `conf.d` are never touched. The websocket
`map $http_upgrade $connection_upgrade` lives once in the base
`infra/nginx/nginx.conf` (http-context, single definition).

## Safety guarantees

- **Idempotent** — rendered bytes are diffed against what is on disk; identical
  state is a no-op and nginx is *not* reloaded.
- **Validated** — the full config is checked with `nginx -t` before any reload.
- **Rollback** — if validation (or the reload itself) fails, the exact previous
  managed file set is restored and a broken config never reaches a live nginx.
- **Concurrency-safe** — applies serialise on a `flock` over the config dir, so
  concurrent workers on the shared volume never interleave writes.
- **Atomic writes** — each file is written to a temp path and `os.replace`d.

## Async + observability

`POST /nginx/reload` (admin) enqueues `reload_nginx_config` and returns a
`task_id`; poll `GET /tasks/{task_id}`. The task result is a JSON payload:

```json
{ "changed": true, "valid": true, "reloaded": true, "rolled_back": false,
  "message": "Applied 2 managed file(s) and reloaded nginx.",
  "managed_files": ["megoopm-proxy-1.conf", "megoopm-upstream-1.conf"],
  "test_output": "...", "reload_output": "..." }
```

Proxy-host CRUD (a later ticket) calls `enqueue_nginx_reload()` after a
successful write so config regenerates automatically. `GET /nginx/preview`
renders the config for current DB state **without** writing or reloading.

## Configuration

| Env | Default | Purpose |
| --- | --- | --- |
| `NGINX_CONFD_DIR` | `/etc/nginx/conf.d` | Where managed configs are written. |
| `NGINX_CERTS_DIR` | `/etc/nginx/certs` | Cert path root referenced by server blocks. |
| `NGINX_MANAGED_PREFIX` | `megoopm-` | Filename prefix that marks managed files. |
| `NGINX_TEST_COMMAND` | `nginx -t` | Validation command. |
| `NGINX_RELOAD_COMMAND` | `nginx -s reload` | Reload command. |

## Reload transport — worker ↔ nginx control channel (MEG-28)

The engine is topology-agnostic: it shells out to `NGINX_TEST_COMMAND` /
`NGINX_RELOAD_COMMAND` and depends only on their **exit code + stderr** to drive
its validate → reload → rollback state machine. *How* those commands reach the
nginx process when the Celery worker and nginx run in separate containers is the
infrastructure decision resolved here.

**Hard constraint:** the worker must **never** mount the Docker socket in
production. `docker exec` over `/var/run/docker.sock` grants the worker full
control of the host's Docker daemon — a container-escape-grade privilege for what
should be a single `nginx -s reload`. It is acceptable **only** on a trusted
single-host dev box.

### Resolved per topology

| Topology | Compose | Transport | Socket? |
| --- | --- | --- | --- |
| **Dev, single host** | `docker-compose.yml` | `docker exec megoopm-nginx openresty … -t / -s reload` over the mounted daemon socket — landed with MEG-32 D4. | yes — **dev-only** |
| **Production / HA** | `docker-compose.ha.yml` | **Co-located reload agent** (Option 1, below): each nginx node is paired with a tiny agent that runs `openresty -t` / `-s reload` *inside the nginx container*, invoked by that node's worker over the internal network. | **no** |

Both channels satisfy the engine's requirement that `nginx -t` runs the **real**
binary + modules that will serve traffic (OpenResty + the CrowdSec bouncer), so a
validation pass can never diverge from what actually reloads.

### Production transport (recommended, socket-free)

**Option 1 — co-located reload agent.** Run the reload *where the nginx binary
lives*, and invoke it *over the network*, never over the Docker socket:

- Each nginx node exposes an **internal-only** reload endpoint. Because the
  reload commands are **fixed** (`openresty … -t`, `openresty … -s reload`) with
  **no** user-controlled arguments, there is no command-injection surface; the
  endpoint is bound to the internal compose/overlay network and gated by a shared
  token (`NGINX_RELOAD_TOKEN`). Simplest form is a `socat` sidecar built from
  `./infra/nginx` sharing the proxy's PID namespace:

  ```yaml
  # per nginx node, in docker-compose.ha.yml
  proxy1:
    build: ./infra/nginx
    # …
  proxy1-reload:                    # co-located agent, same image → same binary+modules
    build: ./infra/nginx
    pid: "service:proxy1"          # shares proxy1's PID ns → -s reload signals its master
    volumes: [ shared_data:/data ] # sees the same rendered conf.d + pidfile
    command: >
      socat TCP-LISTEN:9099,fork,reuseaddr
        EXEC:'/usr/local/openresty/bin/openresty -p /usr/local/openresty/nginx -c /etc/nginx/nginx.conf -t && /usr/local/openresty/bin/openresty -p /usr/local/openresty/nginx -c /etc/nginx/nginx.conf -s reload'
  ```

  The node's worker then wires:

  ```yaml
  worker:                          # co-scheduled with proxy1 on the same node
    environment:
      NGINX_TEST_COMMAND:   "nc -w5 proxy1-reload 9099"   # blocks on exit code + output
      NGINX_RELOAD_COMMAND: "nc -w5 proxy1-reload 9099"
  ```

  Config fans out to every node via the MEG-35 reconcile broadcast (see
  `docs/ha.md`); each node reloads its *own* nginx, so a bad render rolls back
  node-locally while the others keep serving.

**Option 2 — SSH shim.** A locked-down `sshd` in the nginx image with a
`command="…"`-restricted key that only ever runs the two reload commands. Same
guarantees; heavier to operate (key rotation, host keys). Kept as the fallback if
a policy forbids the raw-TCP agent.

Rejected: shipping the openresty binary *into the worker image* (Option "co-locate
single-container") — it drifts from the nginx image's modules over time, so
`nginx -t` on the worker can pass while the real nginx would reject the config.

Until the production channel is wired, the reload half is exercised via the
injectable controller in tests; generation, validation, idempotency and rollback
are fully covered independently of transport.

## Tests

- `tests/test_nginx_render.py` — rendering matrix (pure, no infra).
- `tests/test_nginx_engine.py` — apply / idempotency / validation rollback /
  reload-failure rollback / unmanaged-file safety (+ a real `nginx -t` check
  that runs when nginx is installed).
- `tests/test_nginx_task.py` — the Celery task (eager) end to end.
- `tests/test_nginx_api.py` — admin RBAC on the endpoints.
