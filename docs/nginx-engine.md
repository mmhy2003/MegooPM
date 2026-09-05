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
  Written into the HTTP `conf.d` when a proxy host references the pool, into
  `conf.d/stream/` when a stream does, and into **both** for a pool whose
  context is `both`. `upstream` blocks are context-local — one defined in
  `http {}` is invisible to `stream {}` — so the same name existing in each is
  what a shared pool means, not a collision.

  A pool's `context` (`http` / `stream` / `both`) decides which of those apply,
  and constrains its method: `ip_hash` exists only in `http`, so a
  stream-capable pool cannot use it. The renderer raises rather than emitting
  it, since an invalid directive fails `nginx -t` and rolls back the apply for
  every managed object.
  A `server` block's `proxy_pass` targets either a pool name or a literal
  `host:port` — the root route and each location choose independently.

- `megoopm-proxy-{id}.conf` — the `server {}` block(s): plain `:80`, or a `:443`
  TLS server (+ `:80` redirect when `ssl_forced`) when a certificate is set.
  Honours HSTS, HTTP/2, websocket upgrade, exploit blocking, asset caching and
  free-form `advanced_config`.
  Extra per-path routes (`proxy_host_locations`) render as `location ^~ <path>`
  blocks pointing at their own pool; `^~` makes the longest matching prefix win
  over the asset-caching regex location, so `/api/app.js` reaches the API pool.
  Host-wide options (websockets, forwarded headers, auth stripping) apply to
  every location; a location whose pool has no backends is omitted.

- `megoopm-default.conf` — the **default site**: what nginx answers for a
  request matching no configured host. Written into `NGINX_DEFAULT_DIR`, a
  *sibling* of `conf.d` (not a child, so a future `conf.d/*.conf` glob change
  can never sweep it into `http {}`, where it would be a syntax error). Unlike
  every other generated file this is a bare `location`, not a `server` block:
  the base config includes it from *inside* its `default_server`.

  It holds `return 404` / `return 444` / `return 301 "<url>"`, or a
  `root` + `try_files` pair serving `megoopm-default.html` — written alongside
  it for the two modes that answer with a document (the bundled congratulations
  page, or a Custom Page's HTML). Chosen under Settings; see
  `docs/superpowers/specs/2026-09-01-default-site-design.md`.

  With no file present the request falls through to the default server's
  `root`, which points at a path that does not exist, so the answer is a plain
  404. That `root` is load-bearing: without it OpenResty serves its own welcome
  page instead.

- `megoopm-error-{code}.html` + `megoopm-errors.conf.inc` — the **branded error
  pages**. Eight documents, one per status MegooPM brands (400, 401, 403, 404,
  500, 502, 503, 504), written into `NGINX_DEFAULT_DIR` beside the default
  site, and one fragment that every managed `server {}` includes:

  ```nginx
  error_page 404 /megoopm-error-404.html;
  location = /megoopm-error-404.html { root /data/nginx/default; internal; }
  ```

  `internal` means the document answers only an internal redirect: requesting
  it directly gets a 404, so the page cannot be probed for what is behind it.
  The fragment ends in `.inc`, not `.conf`, so the base config's `conf.d/*.conf`
  glob never parses a bare `error_page` list as a server block.

  Only errors **nginx itself** produces are branded. There is no
  `proxy_intercept_errors`, so a 404 or a 500 from your own application reaches
  the visitor exactly as your application wrote it. What nginx answers on its
  own — an upstream that is down (502), a timeout (504), an access list refusal
  (403) — gets the branded page.

  Each document is self-contained: the palette is inline and the logo is a
  base64 `data:` URI, so a page renders with no network at all, which is the
  state a 502 usually means. Nothing on it names a host, an upstream, a path,
  or anything from the request.

  A code with no row in `error_page` is rendered from the shipped template, so
  a fresh install is fully branded with nothing configured. Binding a code to a
  Custom Page under Settings writes that page's HTML into the same filename.

  A proxy host location can also be answered this way: give it the **Error
  page** target and a status, and it renders as a bare `return <code>;`. The
  server block's own `error_page` mapping turns that into the branded
  document, so the visitor gets the real status and whichever body Settings
  currently says. Nothing per-location is written to disk.

  Verify on a live stack:

  ```bash
  docker compose exec nginx ls /data/nginx/default/
  docker compose exec nginx cat /data/nginx/default/megoopm-errors.conf.inc
  curl -sI https://<a-managed-domain>/definitely-not-here | head -1   # 404
  ```

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
| `NGINX_DEFAULT_DIR` | `{data}/nginx/default` | Default-site fragment + document. |
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
