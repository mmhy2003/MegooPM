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

### Resolved: the in-container reload agent (all topologies)

| Topology | Compose | Transport | Socket? |
| --- | --- | --- | --- |
| Development | `docker-compose.dev.yml` | reload agent | **no** |
| Production, single node | `docker-compose.yml` | reload agent | **no** |
| Production, multi-node | `docker-compose.ha.yml` (per node → its local nginx) | reload agent | **no** |

The nginx image (`infra/nginx`) starts a tiny agent beside OpenResty:
`socat TCP-LISTEN:9099,fork EXEC:/reload-agent.sh`, internal network only,
never published. A request is one line, `<token> <ping|test|reload>`; the agent
checks the token against `NGINX_RELOAD_TOKEN`, runs the **fixed** command
(`openresty -p … -c /etc/nginx/nginx.conf -t` or `-s reload` — no client
arguments ever reach a shell), streams the output and ends with
`__MEGOOPM_STATUS__ <exit code>`. The command's output is captured to a file
first because the image's log files are symlinks to `/dev/stdout`/`/dev/stderr`,
which cannot be reopened through the socket socat hands the script. The worker
side is `python -m scripts.nginx_remote test|reload` (env `NGINX_AGENT_ADDR`,
`NGINX_RELOAD_TOKEN`), which mirrors the output to stderr and exits with the
remote status — so `NGINX_TEST_COMMAND`/`NGINX_RELOAD_COMMAND` point at it and
the engine's validate → reload → rollback logic is unchanged. Same binary, same
modules, same container: `-t` can never diverge from what reloads. The
container healthcheck also `ping`s the agent.

Rejected alternatives: `docker exec` over the daemon socket (container-escape
grade privilege; removed even from dev); a PID-namespace sidecar running the
commands (`-s reload` reads `nginx.pid` from the sidecar's own filesystem, where
it does not exist); shipping the openresty binary into the worker image (module
drift makes `-t` on the worker untrustworthy); an SSH shim (same guarantees,
heavier to operate).

## Tests

- `tests/test_nginx_render.py` — rendering matrix (pure, no infra).
- `tests/test_nginx_engine.py` — apply / idempotency / validation rollback /
  reload-failure rollback / unmanaged-file safety (+ a real `nginx -t` check
  that runs when nginx is installed).
- `tests/test_nginx_task.py` — the Celery task (eager) end to end.
- `tests/test_nginx_api.py` — admin RBAC on the endpoints.
