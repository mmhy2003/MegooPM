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

## Open deployment decision — reload transport ⚠️

The engine is topology-agnostic: it shells out to `NGINX_TEST_COMMAND` /
`NGINX_RELOAD_COMMAND`. **How those commands reach the nginx process when the
Celery worker and nginx run in separate containers is an infrastructure
decision, not resolved by this ticket.** `docker-compose.yml` wires the
*config-generation* path (shared `nginx_confd` / `nginx_certs` volumes + an
`nginx` service), but the base worker image ships no nginx client, so the
default commands are a placeholder there.

Recommended options (escalated to the CEO/infra):

1. **Co-locate** the worker with an nginx binary and share nginx's pid namespace
   (simplest; mirrors upstream Nginx-Proxy-Manager's single-container model).
2. **Sidecar exec / SSH shim** — point the commands at a wrapper that runs
   `nginx -t` / `-s reload` inside the nginx container.

Until this is chosen, the reload half is exercised via the injectable controller
in tests; generation, validation, idempotency and rollback are fully covered.

## Tests

- `tests/test_nginx_render.py` — rendering matrix (pure, no infra).
- `tests/test_nginx_engine.py` — apply / idempotency / validation rollback /
  reload-failure rollback / unmanaged-file safety (+ a real `nginx -t` check
  that runs when nginx is installed).
- `tests/test_nginx_task.py` — the Celery task (eager) end to end.
- `tests/test_nginx_api.py` — admin RBAC on the endpoints.
