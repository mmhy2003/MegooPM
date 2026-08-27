# Deployment Topologies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three self-contained compose files — production single-node (`docker-compose.yml`), development with hot reload (`docker-compose.dev.yml`), per-node multi-node HA (`docker-compose.ha.yml`) — plus the socket-free nginx reload agent and production frontend image they need.

**Architecture:** A `socat`-served agent inside the nginx container runs the fixed `openresty -t` / `-s reload` commands for a token-bearing TCP client (`python -m scripts.nginx_remote`) that the Celery worker uses as `NGINX_TEST_COMMAND`/`NGINX_RELOAD_COMMAND`; the engine is untouched. All topologies share one storage layout under `/data`. The frontend gets a multi-stage Dockerfile with `dev` and `runner` (Next standalone) targets. HA nodes bind-mount `SHARED_DATA_PATH` to `/data`, carry `NODE_ID`, and opt into `control-plane`/`scheduler` profiles via `COMPOSE_PROFILES` in their `.env`.

**Tech Stack:** Docker Compose v2 (profiles, `${VAR:?}` interpolation), OpenResty alpine + socat, Python 3.12 stdlib sockets, Next.js 16 standalone output, uvicorn/watchfiles reload.

**Spec:** `docs/superpowers/specs/2026-08-27-deployment-topologies-design.md`

## Global Constraints

- Backend tests cannot run on this Windows host (`fcntl`); run them in a throwaway Linux container from the built image (the live dev stack must be up):
  ```bash
  MSYS_NO_PATHCONV=1 docker run -d --name megoopm-test --network megoopm_default \
    -e DATABASE_URL="$(docker exec megoopm-backend-1 printenv DATABASE_URL)" \
    -v "C:/Projects/MegooPM/backend:/app" -w /app --entrypoint sleep megoopm-backend infinity
  docker exec megoopm-test pip install --user -q pytest pytest-asyncio aiosqlite ruff
  docker exec megoopm-test python -m pytest -q -p no:warnings tests/test_x.py
  docker rm -f megoopm-test        # when done
  ```
- Backend line length 100; `ruff check` + `ruff format --check` on files you touch (the repo has pre-existing unformatted files — ignore hunks you did not write).
- Shell scripts and Dockerfiles are LF (`.gitattributes` enforces it); never introduce CRLF. Write files with the Write tool or Python `newline="\n"`.
- The Bash tool on this machine mangles `\\n` inside heredocs — use the Write/Edit tools for any file containing backslash escapes.
- Compose project names are fixed by the spec: `megoopm-prod` (production single node), `megoopm` (dev — must stay so existing dev volumes are reused), `megoopm-ha` (per-node HA).
- Storage layout everywhere: `/data/nginx/conf.d`, `/data/nginx/conf.d/stream`, `/data/certs`, `/data/certs/_acme-challenge`; `/data` owned by uid 1000.
- Reload agent protocol (verbatim): request line `<token> <ping|test|reload>`; response = command output then `__MEGOOPM_STATUS__ <exit code>`; agent port `9099`, never published.
- Client exit codes: remote status as-is; `64` missing token; `70` no status line; `111` unreachable/timeout.
- Required production env (`${VAR:?}`): `docker-compose.yml` → `SECRET_KEY`, `POSTGRES_PASSWORD`, `CROWDSEC_BOUNCER_KEY`, `NGINX_RELOAD_TOKEN`, `NEXT_PUBLIC_API_BASE_URL`; `docker-compose.ha.yml` → those minus `POSTGRES_PASSWORD`, plus `NODE_ID`, `SHARED_DATA_PATH`, `DATABASE_URL`, `REDIS_URL`, `CROWDSEC_LAPI_URL`, `CROWDSEC_APPSEC_URL`.
- Commit messages: conventional prefix, end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Never `docker compose down -v` the `megoopm` (dev) project — it holds the user's data.

---

## File Structure

| File | Responsibility |
|---|---|
| `infra/nginx/reload-agent.sh` (new) | per-connection agent: token check, fixed commands, status trailer |
| `infra/nginx/docker-entrypoint.sh` | start the socat agent loop, warn on empty token |
| `infra/nginx/Dockerfile` | add `socat`, copy the agent, healthcheck |
| `infra/nginx/nginx.conf` | include managed files from `/data/...` |
| `backend/scripts/nginx_remote.py` (new) | TCP client used as the worker's test/reload command |
| `backend/tests/test_nginx_remote.py` (new) | client unit tests against a fake agent |
| `frontend/Dockerfile`, `frontend/next.config.ts` | `dev` / `runner` targets, standalone output |
| `docker-compose.dev.yml` (new) | dev stack with hot reload, existing volumes |
| `docker-compose.yml` | rewritten: production single node |
| `docker-compose.ha.yml` | rewritten: per-node production |
| `.env.example`, `.env.ha.example` (new) | env templates |
| `backend/tests/test_compose_config.py` (new) | `docker compose config` smoke for all three files |
| `infra/ha/haproxy.cfg` | external-LB example |
| `Makefile`, `README.md`, `docs/ha.md`, `docs/nginx-engine.md`, `docs/CONVENTIONS.md`, `backend/.env.example` | tooling + docs |
| `backend/docker-compose.yml` | deleted |

---

### Task 1: Reload agent in the nginx image

**Files:**
- Create: `infra/nginx/reload-agent.sh`
- Modify: `infra/nginx/docker-entrypoint.sh`
- Modify: `infra/nginx/Dockerfile`
- Modify: `infra/nginx/nginx.conf:73-90` (the two `include` lines + header comment)

**Interfaces:**
- Produces: TCP agent on `:9099` inside the nginx container speaking the protocol in Global Constraints; env `NGINX_RELOAD_TOKEN` consumed by the container. nginx now includes `/data/nginx/conf.d/*.conf` and `/data/nginx/conf.d/stream/*.conf`.

- [ ] **Step 1: Write the agent script**

Create `infra/nginx/reload-agent.sh` (LF endings):

```sh
#!/bin/sh
# MegooPM nginx reload agent — one process per connection, spawned by socat
# (see docker-entrypoint.sh). stdin/stdout are the TCP socket.
#
# Protocol: the client sends ONE line `<token> <ping|test|reload>`. The agent
# runs the matching FIXED command (no arguments ever come from the client),
# streams its combined output back, and ends with `__MEGOOPM_STATUS__ <code>`.
# The worker-side client (backend/scripts/nginx_remote.py) turns that trailer
# into its own exit code so the reload engine sees `nginx -t` semantics.
#
# Exit codes on the trailer: the command's own status; 2 unknown command;
# 77 refused (bad or unset token).
set -u

OPENRESTY_BIN="/usr/local/openresty/bin/openresty"
OPENRESTY_ARGS="-p /usr/local/openresty/nginx -c /etc/nginx/nginx.conf"

finish() {
    printf '__MEGOOPM_STATUS__ %s\n' "$1"
    exit 0
}

# shellcheck disable=SC2034  # _rest swallows anything after the command word
if ! read -r token cmd _rest; then
    echo "reload agent: empty request"
    finish 2
fi

if [ -z "${NGINX_RELOAD_TOKEN:-}" ]; then
    echo "reload agent: NGINX_RELOAD_TOKEN is not set on the nginx container; refusing"
    finish 77
fi
if [ "$token" != "$NGINX_RELOAD_TOKEN" ]; then
    echo "reload agent: bad token"
    finish 77
fi

case "$cmd" in
    ping)
        echo pong
        finish 0
        ;;
    test)
        # shellcheck disable=SC2086  # OPENRESTY_ARGS is intentionally word-split
        "$OPENRESTY_BIN" $OPENRESTY_ARGS -t 2>&1
        finish $?
        ;;
    reload)
        # shellcheck disable=SC2086
        "$OPENRESTY_BIN" $OPENRESTY_ARGS -s reload 2>&1
        finish $?
        ;;
    *)
        echo "reload agent: unknown command '$cmd'"
        finish 2
        ;;
esac
```

- [ ] **Step 2: Start the agent from the entrypoint**

In `infra/nginx/docker-entrypoint.sh`, replace the final `exec "$@"` with:

```sh
# --- Reload agent (worker <-> nginx control channel, socket-free) ---
# socat spawns /reload-agent.sh per connection on :9099 (never published, so
# only the compose network can reach it). Runs in a restart loop so a crashed
# socat comes back within a second; the container healthcheck pings it.
: "${NGINX_RELOAD_TOKEN:=}"
if [ -z "${NGINX_RELOAD_TOKEN}" ]; then
    echo "[megoopm] WARNING: NGINX_RELOAD_TOKEN is empty — the reload agent will" \
         "refuse every request, so the worker cannot validate/reload this nginx." >&2
fi
export NGINX_RELOAD_TOKEN
(
    while true; do
        socat TCP-LISTEN:9099,fork,reuseaddr EXEC:/reload-agent.sh
        sleep 1
    done
) &

exec "$@"
```

- [ ] **Step 3: Update the Dockerfile**

In `infra/nginx/Dockerfile`:
- Change `RUN apk add --no-cache gettext ca-certificates` to `RUN apk add --no-cache gettext ca-certificates socat` and extend its comment: `# socat serves the reload agent (see reload-agent.sh / docker-entrypoint.sh).`
- After `COPY docker-entrypoint.sh /docker-entrypoint.sh` add `COPY reload-agent.sh /reload-agent.sh` and change the CR-strip line to `RUN sed -i 's/\r$//' /docker-entrypoint.sh /reload-agent.sh && chmod +x /docker-entrypoint.sh /reload-agent.sh`.
- Before `ENTRYPOINT`, add:
  ```dockerfile
  # Healthy = nginx answers /healthz AND the reload agent answers `ping` with the
  # configured token (compose passes NGINX_RELOAD_TOKEN into the container).
  HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
      CMD sh -c 'wget -q --spider http://127.0.0.1/healthz && printf "%s ping\n" "$NGINX_RELOAD_TOKEN" | socat -T3 - TCP:127.0.0.1:9099 | grep -q pong'
  ```

- [ ] **Step 4: Point nginx.conf at /data**

In `infra/nginx/nginx.conf`:
- Header comment: replace the sentence about `/etc/nginx/conf.d` / `/etc/nginx/certs` with: `The MegooPM backend generates one *.conf per managed host into /data/nginx/conf.d (the shared data root, mounted into this container at the same path), and this config include`s them. TLS certs land under /data/certs.`
- `include /etc/nginx/conf.d/*.conf;` → `include /data/nginx/conf.d/*.conf;`
- `include /etc/nginx/conf.d/stream/*.conf;` → `include /data/nginx/conf.d/stream/*.conf;`

- [ ] **Step 5: Build and exercise the agent standalone**

```bash
cd /c/Projects/MegooPM && docker build -t megoopm-nginx-agent-test ./infra/nginx
docker rm -f agent-test >/dev/null 2>&1; docker run -d --name agent-test -e NGINX_RELOAD_TOKEN=t0k -e CROWDSEC_BOUNCER_KEY=x megoopm-nginx-agent-test
sleep 3
docker exec agent-test sh -c 'mkdir -p /data/nginx/conf.d/stream /data/certs'   # empty include dirs
docker exec agent-test sh -c 'printf "t0k ping\n"   | socat -T5 - TCP:127.0.0.1:9099'
docker exec agent-test sh -c 'printf "t0k test\n"   | socat -T5 - TCP:127.0.0.1:9099'
docker exec agent-test sh -c 'printf "t0k reload\n" | socat -T5 - TCP:127.0.0.1:9099'
docker exec agent-test sh -c 'printf "wrong test\n" | socat -T5 - TCP:127.0.0.1:9099'
docker exec agent-test sh -c 'printf "t0k rm -rf\n" | socat -T5 - TCP:127.0.0.1:9099'
docker inspect agent-test --format '{{.State.Health.Status}}'
docker rm -f agent-test
```

Expected, in order: `pong` + `__MEGOOPM_STATUS__ 0`; the `-t` output (`syntax is ok` / `test is successful`) + `__MEGOOPM_STATUS__ 0`; `__MEGOOPM_STATUS__ 0` for reload; `reload agent: bad token` + `__MEGOOPM_STATUS__ 77`; `reload agent: unknown command 'rm'` + `__MEGOOPM_STATUS__ 2`; health `healthy` (may need one more `sleep 15` before inspecting).

- [ ] **Step 6: Commit**

```bash
git add infra/nginx/reload-agent.sh infra/nginx/docker-entrypoint.sh infra/nginx/Dockerfile infra/nginx/nginx.conf
git commit -m "feat(nginx): in-container reload agent; managed includes move to /data" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `scripts.nginx_remote` client

**Files:**
- Create: `backend/scripts/nginx_remote.py`
- Test: `backend/tests/test_nginx_remote.py`

**Interfaces:**
- Produces: `run(command: str, *, addr: str, token: str, timeout: float) -> int` (returns the exit code; raises `OSError` on connection problems) and `main(argv: list[str] | None = None) -> int`; module constants `EXIT_USAGE = 64`, `EXIT_NO_STATUS = 70`, `EXIT_UNAVAILABLE = 111`, `STATUS_PREFIX = "__MEGOOPM_STATUS__ "`. CLI: `python -m scripts.nginx_remote <ping|test|reload>` reading `NGINX_AGENT_ADDR` (default `nginx:9099`), `NGINX_RELOAD_TOKEN`, `NGINX_AGENT_TIMEOUT_SECONDS` (default `30`).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_nginx_remote.py`:

```python
"""The worker-side client for the nginx reload agent (scripts/nginx_remote.py).

A fake agent (threaded TCP server) scripts the responses so the client's exit
code mapping and output mirroring are pinned down without nginx or Docker.
"""

from __future__ import annotations

import socket
import socketserver
import threading
import time
from collections.abc import Callable, Iterator

import pytest
from scripts import nginx_remote


class _FakeAgent:
    """Accepts one line and replies with whatever ``script(line)`` returns."""

    def __init__(self, script: Callable[[str], bytes | None]) -> None:
        agent = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                line = self.rfile.readline().decode()
                agent.requests.append(line)
                reply = script(line)
                if reply is not None:
                    self.wfile.write(reply)

        self.requests: list[str] = []
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def addr(self) -> str:
        host, port = self.server.server_address[:2]
        return f"{host}:{port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def agent() -> Iterator[Callable[[Callable[[str], bytes | None]], _FakeAgent]]:
    agents: list[_FakeAgent] = []

    def make(script: Callable[[str], bytes | None]) -> _FakeAgent:
        a = _FakeAgent(script)
        agents.append(a)
        return a

    yield make
    for a in agents:
        a.close()


def test_ping_sends_token_and_returns_remote_status(agent, capsys) -> None:
    a = agent(lambda line: b"pong\n__MEGOOPM_STATUS__ 0\n")
    assert nginx_remote.run("ping", addr=a.addr, token="t0k", timeout=2) == 0
    assert a.requests == ["t0k ping\n"]
    assert capsys.readouterr().err == "pong\n"


def test_non_zero_status_and_output_mirrored_to_stderr(agent, capsys) -> None:
    a = agent(lambda line: b"nginx: [emerg] unknown directive\nnginx: test failed\n__MEGOOPM_STATUS__ 1\n")
    assert nginx_remote.run("test", addr=a.addr, token="t0k", timeout=2) == 1
    err = capsys.readouterr().err
    assert "unknown directive" in err
    assert "__MEGOOPM_STATUS__" not in err


def test_missing_status_line_is_70(agent, capsys) -> None:
    a = agent(lambda line: b"connection dropped mid-way\n")
    assert nginx_remote.run("test", addr=a.addr, token="t0k", timeout=2) == nginx_remote.EXIT_NO_STATUS
    assert "no status" in capsys.readouterr().err


def test_main_maps_connection_failures_to_111(monkeypatch, capsys) -> None:
    # Bind then close a socket to find a port nobody listens on.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    monkeypatch.setenv("NGINX_AGENT_ADDR", f"127.0.0.1:{port}")
    monkeypatch.setenv("NGINX_RELOAD_TOKEN", "t0k")
    monkeypatch.setenv("NGINX_AGENT_TIMEOUT_SECONDS", "1")
    assert nginx_remote.main(["test"]) == nginx_remote.EXIT_UNAVAILABLE
    assert f"127.0.0.1:{port}" in capsys.readouterr().err


def test_main_times_out_to_111(agent, monkeypatch) -> None:
    def slow(line: str) -> bytes | None:
        time.sleep(2)
        return b"__MEGOOPM_STATUS__ 0\n"

    a = agent(slow)
    monkeypatch.setenv("NGINX_AGENT_ADDR", a.addr)
    monkeypatch.setenv("NGINX_RELOAD_TOKEN", "t0k")
    monkeypatch.setenv("NGINX_AGENT_TIMEOUT_SECONDS", "0.3")
    assert nginx_remote.main(["ping"]) == nginx_remote.EXIT_UNAVAILABLE


def test_main_requires_token_and_a_known_command(monkeypatch, capsys) -> None:
    monkeypatch.delenv("NGINX_RELOAD_TOKEN", raising=False)
    monkeypatch.setenv("NGINX_AGENT_ADDR", "127.0.0.1:1")
    assert nginx_remote.main(["test"]) == nginx_remote.EXIT_USAGE
    assert "NGINX_RELOAD_TOKEN" in capsys.readouterr().err
    monkeypatch.setenv("NGINX_RELOAD_TOKEN", "t0k")
    with pytest.raises(SystemExit):  # argparse rejects unknown choices
        nginx_remote.main(["rm"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker exec megoopm-test python -m pytest -q -p no:warnings tests/test_nginx_remote.py`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'scripts.nginx_remote'`

- [ ] **Step 3: Implement the client**

Create `backend/scripts/nginx_remote.py`:

```python
"""Worker-side client for the nginx reload agent (infra/nginx/reload-agent.sh).

The Celery worker and the managed nginx run in different containers. Instead
of mounting the Docker socket, the nginx container serves a tiny token-gated
TCP agent on :9099 that runs the FIXED ``openresty -t`` / ``-s reload``
commands. This script is what ``NGINX_TEST_COMMAND`` / ``NGINX_RELOAD_COMMAND``
point at::

    python -m scripts.nginx_remote test
    python -m scripts.nginx_remote reload
    python -m scripts.nginx_remote ping

Environment: ``NGINX_AGENT_ADDR`` (default ``nginx:9099``), ``NGINX_RELOAD_TOKEN``
(required), ``NGINX_AGENT_TIMEOUT_SECONDS`` (default ``30``).

The agent's output is mirrored to **stderr** (the reload engine reads
``nginx -t`` diagnostics from stderr) and its trailing ``__MEGOOPM_STATUS__ N``
line becomes this process's exit code, so the engine's validate → reload →
rollback logic is unchanged. Exit codes of our own: 64 usage (no token),
70 the agent sent no status line, 111 the agent is unreachable / timed out.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys

STATUS_PREFIX = "__MEGOOPM_STATUS__ "
COMMANDS = ("ping", "test", "reload")
EXIT_USAGE = 64
EXIT_NO_STATUS = 70
EXIT_UNAVAILABLE = 111
DEFAULT_ADDR = "nginx:9099"
DEFAULT_TIMEOUT = 30.0


def _split_addr(addr: str) -> tuple[str, int]:
    host, sep, port = addr.rpartition(":")
    if not sep or not port.isdigit():
        raise ValueError(f"NGINX_AGENT_ADDR must be host:port, got {addr!r}")
    return host, int(port)


def run(command: str, *, addr: str, token: str, timeout: float) -> int:
    """Send ``command`` to the agent; mirror its output; return the remote status.

    Raises ``OSError`` (incl. ``socket.timeout``) when the agent cannot be
    reached — ``main`` maps that to :data:`EXIT_UNAVAILABLE`.
    """
    host, port = _split_addr(addr)
    chunks: list[bytes] = []
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(f"{token} {command}\n".encode())
        while True:
            data = sock.recv(4096)
            if not data:
                break
            chunks.append(data)
    lines = b"".join(chunks).decode("utf-8", "replace").splitlines()
    status: int | None = None
    for line in reversed(lines):
        if line.startswith(STATUS_PREFIX):
            raw = line[len(STATUS_PREFIX) :].strip()
            status = int(raw) if raw.isdigit() else None
            break
    output = [line for line in lines if not line.startswith(STATUS_PREFIX)]
    if output:
        print("\n".join(output), file=sys.stderr)
    if status is None:
        print("nginx_remote: agent returned no status line", file=sys.stderr)
        return EXIT_NO_STATUS
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nginx_remote", description="Validate or reload the managed nginx via its agent."
    )
    parser.add_argument("command", choices=COMMANDS)
    args = parser.parse_args(argv)

    token = os.environ.get("NGINX_RELOAD_TOKEN", "")
    if not token:
        print("nginx_remote: NGINX_RELOAD_TOKEN is not set", file=sys.stderr)
        return EXIT_USAGE
    addr = os.environ.get("NGINX_AGENT_ADDR", DEFAULT_ADDR)
    timeout = float(os.environ.get("NGINX_AGENT_TIMEOUT_SECONDS", DEFAULT_TIMEOUT))
    try:
        return run(args.command, addr=addr, token=token, timeout=timeout)
    except (OSError, ValueError) as exc:
        print(f"nginx_remote: cannot reach the reload agent at {addr}: {exc}", file=sys.stderr)
        return EXIT_UNAVAILABLE


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker exec megoopm-test python -m pytest -q -p no:warnings tests/test_nginx_remote.py`
Expected: PASS (6 tests)

Run: `docker exec megoopm-test sh -c 'python -m ruff check scripts/nginx_remote.py tests/test_nginx_remote.py && python -m ruff format --check scripts/nginx_remote.py tests/test_nginx_remote.py'`
Expected: clean (if `ruff format` wants the long test line wrapped, wrap it)

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/nginx_remote.py backend/tests/test_nginx_remote.py
git commit -m "feat(nginx): worker-side client for the reload agent" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Frontend production image

**Files:**
- Modify: `frontend/next.config.ts`
- Modify: `frontend/Dockerfile` (rewrite)

**Interfaces:**
- Produces: build targets `dev` (runs `next dev`, expects `/app` bind mount) and `runner` (standalone server on :3000); build args `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_AUTH_ENABLED`.

- [ ] **Step 1: Enable standalone output**

`frontend/next.config.ts`:

```ts
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Self-contained server bundle for the production image (frontend/Dockerfile,
  // `runner` stage): `.next/standalone/server.js` + traced node_modules only.
  output: "standalone",
};

export default nextConfig;
```

- [ ] **Step 2: Rewrite the Dockerfile**

`frontend/Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1
# MegooPM frontend image — two targets:
#   dev     `next dev` with the source bind-mounted (docker-compose.dev.yml)
#   runner  Next.js standalone server (docker-compose.yml / docker-compose.ha.yml)
#
# NEXT_PUBLIC_* values are inlined at BUILD time by Next, so the production
# targets take them as build args (compose passes them from .env). Changing
# NEXT_PUBLIC_API_BASE_URL therefore needs `docker compose build frontend`.

FROM node:22-alpine AS deps
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# --- Development: hot reload, source arrives via bind mount ---------------
FROM deps AS dev
ENV NODE_ENV=development
COPY . .
EXPOSE 3000
# Bind to 0.0.0.0 so the dev server is reachable from the host / other
# containers, not just localhost inside the container.
CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0"]

# --- Production build -----------------------------------------------------
FROM deps AS builder
ARG NEXT_PUBLIC_API_BASE_URL
ARG NEXT_PUBLIC_AUTH_ENABLED=false
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL} \
    NEXT_PUBLIC_AUTH_ENABLED=${NEXT_PUBLIC_AUTH_ENABLED} \
    NODE_ENV=production
COPY . .
RUN npm run build

# --- Production runtime: standalone server, non-root ----------------------
FROM node:22-alpine AS runner
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
WORKDIR /app
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public
USER node
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD wget -qO- http://127.0.0.1:3000/ >/dev/null || exit 1
CMD ["node", "server.js"]
```

- [ ] **Step 3: Build both targets and smoke the runner**

```bash
cd /c/Projects/MegooPM/frontend
docker build --target dev -t megoopm-frontend-dev-test . 2>&1 | tail -2
docker build --target runner --build-arg NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 -t megoopm-frontend-runner-test . 2>&1 | tail -2
docker rm -f fe-test >/dev/null 2>&1; docker run -d --name fe-test -p 13000:3000 megoopm-frontend-runner-test
sleep 5; curl -s -o /dev/null -w "runner http %{http_code}\n" http://localhost:13000/login
docker exec fe-test sh -c 'id -u; ls server.js .next/static public | head -3'
docker rm -f fe-test
```

Expected: both builds succeed; `runner http 200`; uid `1000` (node), `server.js` present.

- [ ] **Step 4: Frontend gates still pass**

Run (in `frontend/`): `npm run lint && npm run typecheck && npm test`
Expected: clean

- [ ] **Step 5: Commit**

```bash
git add frontend/Dockerfile frontend/next.config.ts
git commit -m "feat(frontend): multi-stage image with dev and standalone runner targets" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `docker-compose.dev.yml`, Makefile, live switch of the dev stack

**Files:**
- Create: `docker-compose.dev.yml`
- Modify: `Makefile`
- Delete: `backend/docker-compose.yml`

**Interfaces:**
- Consumes: agent (Task 1), client (Task 2), `dev` target (Task 3).
- Produces: the dev stack under project `megoopm`, services `db`, `redis`, `crowdsec`, `data-init`, `backend`, `worker`, `beat`, `frontend`, `nginx`; volumes `pgdata`, `nginx_confd`, `nginx_certs`, `crowdsec_config`, `crowdsec_data`.

- [ ] **Step 1: Write the dev compose file**

Create `docker-compose.dev.yml`:

```yaml
# MegooPM — development stack with hot reload.
#
#   docker compose -f docker-compose.dev.yml up --build     # or: make up
#
# * backend / worker: ./backend is bind-mounted; uvicorn runs with --reload and
#   the Celery worker restarts under `watchfiles` when Python files change.
# * frontend: ./frontend is bind-mounted into the `dev` image target (next dev).
# * Polling is on for both so edits propagate through Docker Desktop bind mounts
#   on Windows/macOS.
#
# The project name stays `megoopm` on purpose: the named volumes the previous
# docker-compose.yml created (pgdata, nginx_confd, nginx_certs, crowdsec_*)
# are reused, so your dev database, certificates and hosts survive.
#
# Production: docker-compose.yml (single node) / docker-compose.ha.yml (per node).
# Every variable has a safe dev default; a .env is optional (see .env.example).

name: megoopm

x-backend-env: &backend-env
  ENVIRONMENT: ${ENVIRONMENT:-development}
  DEBUG: ${DEBUG:-true}
  SECRET_KEY: ${SECRET_KEY:-change-me-in-production}
  DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-megoopm}:${POSTGRES_PASSWORD:-megoopm}@db:5432/${POSTGRES_DB:-megoopm}
  REDIS_URL: redis://redis:6379/0
  CELERY_BROKER_URL: redis://redis:6379/0
  CELERY_RESULT_BACKEND: redis://redis:6379/1
  CORS_ORIGINS: ${CORS_ORIGINS:-http://localhost:3000}
  # One storage layout in every topology (docs/ha.md §2): rendered vhosts,
  # stream forwards and certs live under /data; nginx includes them from there.
  SHARED_DATA_DIR: /data
  HA_ENABLED: "false"
  HA_LOCK_DIR: /var/run/megoopm
  # Worker -> nginx validate/reload goes through the in-container reload agent
  # (infra/nginx/reload-agent.sh); no Docker socket, not even in dev.
  NGINX_TEST_COMMAND: "python -m scripts.nginx_remote test"
  NGINX_RELOAD_COMMAND: "python -m scripts.nginx_remote reload"
  NGINX_AGENT_ADDR: nginx:9099
  NGINX_RELOAD_TOKEN: ${NGINX_RELOAD_TOKEN:-megoopm-dev-reload-token}
  # ACME: staging by default in dev (unthrottled, untrusted certificates).
  ACME_DIRECTORY_URL: ${ACME_DIRECTORY_URL:-https://acme-staging-v02.api.letsencrypt.org/directory}
  ACME_ACCOUNT_EMAIL: ${ACME_ACCOUNT_EMAIL:-}
  ACME_DNS_PROPAGATION_TIMEOUT_SECONDS: ${ACME_DNS_PROPAGATION_TIMEOUT_SECONDS:-120}
  ACME_DNS_PROPAGATION_INTERVAL_SECONDS: ${ACME_DNS_PROPAGATION_INTERVAL_SECONDS:-5}
  ACME_DNS_PROPAGATION_SETTLE_SECONDS: ${ACME_DNS_PROPAGATION_SETTLE_SECONDS:-10}
  # CrowdSec LAPI (docs/crowdsec.md). Machine creds are DB-backed; the two
  # optional vars are a one-time bootstrap seed.
  CROWDSEC_LAPI_URL: http://crowdsec:8080
  CROWDSEC_LAPI_KEY: ${CROWDSEC_BOUNCER_KEY:-megoopm-dev-bouncer-key}
  CROWDSEC_MACHINE_ID: ${CROWDSEC_MACHINE_ID:-}
  CROWDSEC_MACHINE_PASSWORD: ${CROWDSEC_MACHINE_PASSWORD:-}
  # Initial admin, seeded only while the users table is empty.
  FIRST_ADMIN_EMAIL: ${FIRST_ADMIN_EMAIL:-admin@example.com}
  FIRST_ADMIN_PASSWORD: ${FIRST_ADMIN_PASSWORD:-changeme}
  # inotify events do not cross Docker Desktop bind mounts: poll instead.
  WATCHFILES_FORCE_POLLING: "true"

services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-megoopm}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-megoopm}
      POSTGRES_DB: ${POSTGRES_DB:-megoopm}
    ports:
      - "${POSTGRES_PORT:-5432}:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-megoopm} -d ${POSTGRES_DB:-megoopm}"]
      interval: 5s
      timeout: 5s
      retries: 10

  redis:
    image: redis:7-alpine
    ports:
      - "${REDIS_PORT:-6379}:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  # One-shot: lay out /data and hand it to the backend user (uid 1000) so the
  # backend/worker can write renders and certs while nginx (root) reads them.
  data-init:
    image: busybox:1.36
    command: >
      sh -c "mkdir -p /data/nginx/conf.d/stream /data/certs/_acme-challenge
             && chown -R 1000:1000 /data
             || { echo 'data-init: cannot prepare /data for uid 1000' >&2; exit 1; }"
    volumes:
      - nginx_confd:/data/nginx/conf.d
      - nginx_certs:/data/certs

  backend:
    build: ./backend
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
    environment: *backend-env
    ports:
      - "${BACKEND_PORT:-8000}:8000"
    volumes:
      - ./backend:/app
      - nginx_confd:/data/nginx/conf.d
      - nginx_certs:/data/certs
      - /var/run/megoopm
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
      data-init:
        condition: service_completed_successfully
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/health"]
      interval: 15s
      timeout: 5s
      start_period: 20s
      retries: 5

  worker:
    build: ./backend
    # `watchfiles` restarts the Celery worker when a .py file under app/ changes.
    command:
      [
        "watchfiles",
        "--filter", "python",
        "celery -A app.core.celery_app.celery_app worker --loglevel=info",
        "app",
      ]
    environment:
      <<: *backend-env
      RUN_MIGRATIONS: "0"
    volumes:
      - ./backend:/app
      - nginx_confd:/data/nginx/conf.d
      - nginx_certs:/data/certs
      - /var/run/megoopm
    depends_on:
      redis:
        condition: service_healthy
      backend:
        condition: service_healthy
      nginx:
        condition: service_started
      data-init:
        condition: service_completed_successfully
    healthcheck:
      test: ["CMD", "celery", "-A", "app.core.celery_app.celery_app", "inspect", "ping"]
      interval: 20s
      timeout: 10s
      start_period: 25s
      retries: 5

  beat:
    build: ./backend
    command: ["celery", "-A", "app.core.celery_app.celery_app", "beat", "--loglevel=info"]
    environment:
      <<: *backend-env
      RUN_MIGRATIONS: "0"
    volumes:
      - ./backend:/app
    # No honest liveness probe for the scheduler (MEG-41); watch its logs.
    healthcheck:
      disable: true
    depends_on:
      redis:
        condition: service_healthy

  frontend:
    build:
      context: ./frontend
      target: dev
    environment:
      NEXT_PUBLIC_API_BASE_URL: ${NEXT_PUBLIC_API_BASE_URL:-http://localhost:8000}
      NEXT_PUBLIC_AUTH_ENABLED: ${NEXT_PUBLIC_AUTH_ENABLED:-false}
      WATCHPACK_POLLING: "true"
      CHOKIDAR_USEPOLLING: "true"
    ports:
      - "${FRONTEND_PORT:-3000}:3000"
    volumes:
      - ./frontend:/app
      # Keep the image's installed dependencies and build cache out of the
      # bind mount (anonymous volumes shadow those paths).
      - /app/node_modules
      - /app/.next
    depends_on:
      backend:
        condition: service_started
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://127.0.0.1:3000/"]
      interval: 15s
      timeout: 15s
      start_period: 60s
      retries: 10

  # The managed nginx (OpenResty + CrowdSec bouncer + reload agent).
  nginx:
    build: ./infra/nginx
    environment:
      CROWDSEC_LAPI_URL: http://crowdsec:8080
      CROWDSEC_APPSEC_URL: http://crowdsec:7422
      CROWDSEC_BOUNCER_KEY: ${CROWDSEC_BOUNCER_KEY:-megoopm-dev-bouncer-key}
      NGINX_RELOAD_TOKEN: ${NGINX_RELOAD_TOKEN:-megoopm-dev-reload-token}
    ports:
      - "${NGINX_HTTP_PORT:-8080}:80"
      - "${NGINX_HTTPS_PORT:-8443}:443"
    volumes:
      - ./infra/nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - nginx_confd:/data/nginx/conf.d
      - nginx_certs:/data/certs:ro
    depends_on:
      data-init:
        condition: service_completed_successfully
      crowdsec:
        condition: service_started

  crowdsec:
    image: crowdsecurity/crowdsec:v1.6.4
    environment:
      DISABLE_ONLINE_API: "true"
      BOUNCER_KEY_megoopm: ${CROWDSEC_BOUNCER_KEY:-megoopm-dev-bouncer-key}
      COLLECTIONS: "crowdsecurity/appsec-virtual-patching crowdsecurity/appsec-generic-rules crowdsecurity/nginx"
    expose:
      - "8080"
      - "7422"
    volumes:
      - crowdsec_config:/etc/crowdsec
      - crowdsec_data:/var/lib/crowdsec/data
      # Bind the acquis FILE, not the directory (MEG-36 Defect A).
      - ./infra/crowdsec/acquis/appsec.yaml:/etc/crowdsec/acquis.d/appsec.yaml:ro

volumes:
  pgdata:
  nginx_confd:
  nginx_certs:
  crowdsec_config:
  crowdsec_data:
```

- [ ] **Step 2: Rewrite the Makefile**

```makefile
# MegooPM shortcuts. Thin wrappers over `docker compose`.
#   make help          this list
#   make up / down …   the DEVELOPMENT stack (docker-compose.dev.yml)
#   make prod-*        production, single node (docker-compose.yml)
#   make ha-*          production, this node of a cluster (docker-compose.ha.yml)

COMPOSE      := docker compose -f docker-compose.dev.yml
COMPOSE_PROD := docker compose -f docker-compose.yml
COMPOSE_HA   := docker compose -f docker-compose.ha.yml

.DEFAULT_GOAL := help
.PHONY: help up up-fg down clean build ps logs migrate shell psql redis-cli \
        prod-up prod-down prod-logs ha-up ha-down ha-logs

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up: ## Dev: build and start the stack with hot reload (background)
	$(COMPOSE) up --build -d --remove-orphans

up-fg: ## Dev: same, in the foreground (streams logs)
	$(COMPOSE) up --build --remove-orphans

down: ## Dev: stop and remove containers (volumes are kept)
	$(COMPOSE) down --remove-orphans

clean: ## Dev: stop and remove containers AND named volumes (wipes DB + certs)
	$(COMPOSE) down -v --remove-orphans

build: ## Dev: rebuild all images
	$(COMPOSE) build

ps: ## Dev: show service status and health
	$(COMPOSE) ps

logs: ## Dev: follow logs (make logs s=backend for one service)
	$(COMPOSE) logs -f $(s)

migrate: ## Dev: apply DB migrations against the running backend
	$(COMPOSE) exec backend alembic upgrade head

shell: ## Dev: open a shell in the backend container
	$(COMPOSE) exec backend bash

psql: ## Dev: open a psql session against the dev database
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-megoopm} -d $${POSTGRES_DB:-megoopm}

redis-cli: ## Dev: open a redis-cli session
	$(COMPOSE) exec redis redis-cli

prod-up: ## Prod (single node): build and start (needs a filled-in .env)
	$(COMPOSE_PROD) up --build -d

prod-down: ## Prod (single node): stop (volumes are kept)
	$(COMPOSE_PROD) down

prod-logs: ## Prod (single node): follow logs (s=service)
	$(COMPOSE_PROD) logs -f $(s)

ha-up: ## HA (this node): build and start (needs this node's .env, see .env.ha.example)
	$(COMPOSE_HA) up --build -d

ha-down: ## HA (this node): stop
	$(COMPOSE_HA) down

ha-logs: ## HA (this node): follow logs (s=service)
	$(COMPOSE_HA) logs -f $(s)
```

Delete `backend/docker-compose.yml` (`git rm backend/docker-compose.yml`).

- [ ] **Step 3: Validate the file**

Run: `cd /c/Projects/MegooPM && docker compose -f docker-compose.dev.yml config -q && echo "dev compose ok"`
Expected: `dev compose ok`

- [ ] **Step 4: Migrate the existing dev volumes and switch the running stack**

The old renders on `nginx_confd` reference `/etc/nginx/certs/...`; rewrite them once so the new nginx (which sees certs at `/data/certs`) starts cleanly:

```bash
cd /c/Projects/MegooPM
docker compose -f docker-compose.yml down --remove-orphans          # old dev stack (project megoopm), volumes kept
docker run --rm -v megoopm_nginx_confd:/c alpine sh -c "sed -i 's#/etc/nginx/certs#/data/certs#g' /c/*.conf /c/stream/*.conf 2>/dev/null; grep -l '/data/certs' /c/*.conf || true"
docker compose -f docker-compose.dev.yml up --build -d --remove-orphans
sleep 40; docker compose -f docker-compose.dev.yml ps
```

Expected: every service `healthy` (frontend may take ~60 s; beat has no healthcheck). `docker volume ls | grep megoopm_` still lists the pre-existing volumes.

- [ ] **Step 5: Prove the agent path and hot reload**

```bash
docker compose -f docker-compose.dev.yml exec worker python -m scripts.nginx_remote ping; echo "exit=$?"
docker compose -f docker-compose.dev.yml exec worker python -m scripts.nginx_remote test; echo "exit=$?"
docker compose -f docker-compose.dev.yml exec worker python -c "from app.tasks.nginx import reload_nginx_config; print(reload_nginx_config())"
# hot reload: touch a backend file and watch uvicorn restart
echo "# hot-reload probe" >> backend/app/api/router.py && sleep 5 && docker compose -f docker-compose.dev.yml logs --since 20s backend | grep -iE "reload|detected|Started server" | tail -3
git checkout backend/app/api/router.py
docker compose -f docker-compose.dev.yml logs --since 60s worker | grep -iE "watchfiles|celery@" | tail -3
```

Expected: `pong` / `exit=0`; the `-t` success lines / `exit=0`; the task result dict has `"valid": True` (and `"reloaded": True` the first time because the cert paths changed); uvicorn logs a reload; the worker log shows `watchfiles` watching `app` and a `celery@…` banner.

Also log in at http://localhost:3000 and confirm existing hosts/certs are still there.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.dev.yml Makefile
git rm -q backend/docker-compose.yml
git commit -m "feat(compose): development stack with hot reload and the reload agent" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: `docker-compose.yml` — production single node, `.env.example`

**Files:**
- Modify: `docker-compose.yml` (rewrite)
- Modify: `.env.example` (rewrite)

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: project `megoopm-prod`; services `db`, `redis`, `crowdsec`, `data-init`, `backend`, `worker`, `beat`, `frontend`, `nginx`; volumes `pgdata`, `app_data`, `crowdsec_config`, `crowdsec_data`.

- [ ] **Step 1: Write the production compose file**

`docker-compose.yml`:

```yaml
# MegooPM — PRODUCTION, single node.
#
#   cp .env.example .env     # fill in the "required" section
#   docker compose up -d --build         # or: make prod-up
#
# Everything runs on this host: Postgres, Redis, CrowdSec, the API, the Celery
# worker + beat, the web UI (Next.js standalone build) and the managed nginx.
# No Docker socket is mounted anywhere: the worker validates/reloads nginx via
# the token-gated agent inside the nginx container (docs/nginx-engine.md).
#
# Development with hot reload: docker-compose.dev.yml. Multi-node: docker-compose.ha.yml.

name: megoopm-prod

x-backend-env: &backend-env
  ENVIRONMENT: production
  DEBUG: "false"
  SECRET_KEY: ${SECRET_KEY:?SECRET_KEY is required (openssl rand -hex 32)}
  DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-megoopm}:${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}@db:5432/${POSTGRES_DB:-megoopm}
  REDIS_URL: redis://redis:6379/0
  CELERY_BROKER_URL: redis://redis:6379/0
  CELERY_RESULT_BACKEND: redis://redis:6379/1
  # Must be the origin the browser loads the UI from.
  CORS_ORIGINS: ${CORS_ORIGINS:-http://localhost:3000}
  SHARED_DATA_DIR: /data
  HA_ENABLED: "false"
  HA_LOCK_DIR: /var/run/megoopm
  NGINX_TEST_COMMAND: "python -m scripts.nginx_remote test"
  NGINX_RELOAD_COMMAND: "python -m scripts.nginx_remote reload"
  NGINX_AGENT_ADDR: nginx:9099
  NGINX_RELOAD_TOKEN: ${NGINX_RELOAD_TOKEN:?NGINX_RELOAD_TOKEN is required (openssl rand -hex 32)}
  # Real certificates by default; point at the staging directory to test.
  ACME_DIRECTORY_URL: ${ACME_DIRECTORY_URL:-https://acme-v02.api.letsencrypt.org/directory}
  ACME_ACCOUNT_EMAIL: ${ACME_ACCOUNT_EMAIL:-}
  ACME_DNS_PROPAGATION_TIMEOUT_SECONDS: ${ACME_DNS_PROPAGATION_TIMEOUT_SECONDS:-120}
  ACME_DNS_PROPAGATION_INTERVAL_SECONDS: ${ACME_DNS_PROPAGATION_INTERVAL_SECONDS:-5}
  ACME_DNS_PROPAGATION_SETTLE_SECONDS: ${ACME_DNS_PROPAGATION_SETTLE_SECONDS:-10}
  CROWDSEC_LAPI_URL: http://crowdsec:8080
  CROWDSEC_LAPI_KEY: ${CROWDSEC_BOUNCER_KEY:?CROWDSEC_BOUNCER_KEY is required (openssl rand -hex 32)}
  CROWDSEC_MACHINE_ID: ${CROWDSEC_MACHINE_ID:-}
  CROWDSEC_MACHINE_PASSWORD: ${CROWDSEC_MACHINE_PASSWORD:-}
  # No default admin in production. Either set both here before the first
  # start, or seed later with: docker compose exec backend python -m scripts.create_user …
  FIRST_ADMIN_EMAIL: ${FIRST_ADMIN_EMAIL:-}
  FIRST_ADMIN_PASSWORD: ${FIRST_ADMIN_PASSWORD:-}

services:
  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-megoopm}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}
      POSTGRES_DB: ${POSTGRES_DB:-megoopm}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-megoopm} -d ${POSTGRES_DB:-megoopm}"]
      interval: 5s
      timeout: 5s
      retries: 10

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  data-init:
    image: busybox:1.36
    command: >
      sh -c "mkdir -p /data/nginx/conf.d/stream /data/certs/_acme-challenge
             && chown -R 1000:1000 /data
             || { echo 'data-init: cannot prepare /data for uid 1000' >&2; exit 1; }"
    volumes:
      - app_data:/data

  backend:
    build: ./backend
    restart: unless-stopped
    environment:
      <<: *backend-env
      RUN_MIGRATIONS: "1"
    ports:
      - "${BACKEND_PORT:-8000}:8000"
    volumes:
      - app_data:/data
      - /var/run/megoopm
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
      data-init:
        condition: service_completed_successfully
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/health"]
      interval: 15s
      timeout: 5s
      start_period: 20s
      retries: 5

  worker:
    build: ./backend
    restart: unless-stopped
    command: ["celery", "-A", "app.core.celery_app.celery_app", "worker", "--loglevel=info"]
    environment:
      <<: *backend-env
      RUN_MIGRATIONS: "0"
    volumes:
      - app_data:/data
      - /var/run/megoopm
    depends_on:
      redis:
        condition: service_healthy
      backend:
        condition: service_healthy
      nginx:
        condition: service_started
    healthcheck:
      test: ["CMD", "celery", "-A", "app.core.celery_app.celery_app", "inspect", "ping"]
      interval: 20s
      timeout: 10s
      start_period: 25s
      retries: 5

  beat:
    build: ./backend
    restart: unless-stopped
    command: ["celery", "-A", "app.core.celery_app.celery_app", "beat", "--loglevel=info"]
    environment:
      <<: *backend-env
      RUN_MIGRATIONS: "0"
    healthcheck:
      disable: true
    depends_on:
      redis:
        condition: service_healthy

  frontend:
    build:
      context: ./frontend
      target: runner
      args:
        # Inlined at build time by Next: rebuild the image after changing them.
        NEXT_PUBLIC_API_BASE_URL: ${NEXT_PUBLIC_API_BASE_URL:?NEXT_PUBLIC_API_BASE_URL is required (the public URL of the API, e.g. https://megoopm-api.example.com)}
        NEXT_PUBLIC_AUTH_ENABLED: ${NEXT_PUBLIC_AUTH_ENABLED:-true}
    restart: unless-stopped
    ports:
      - "${FRONTEND_PORT:-3000}:3000"
    depends_on:
      backend:
        condition: service_started

  nginx:
    build: ./infra/nginx
    restart: unless-stopped
    environment:
      CROWDSEC_LAPI_URL: http://crowdsec:8080
      CROWDSEC_APPSEC_URL: http://crowdsec:7422
      CROWDSEC_BOUNCER_KEY: ${CROWDSEC_BOUNCER_KEY:?CROWDSEC_BOUNCER_KEY is required}
      NGINX_RELOAD_TOKEN: ${NGINX_RELOAD_TOKEN:?NGINX_RELOAD_TOKEN is required}
    ports:
      - "${NGINX_HTTP_PORT:-80}:80"
      - "${NGINX_HTTPS_PORT:-443}:443"
    volumes:
      - ./infra/nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - app_data:/data
    depends_on:
      data-init:
        condition: service_completed_successfully
      crowdsec:
        condition: service_started

  crowdsec:
    image: crowdsecurity/crowdsec:v1.6.4
    restart: unless-stopped
    environment:
      DISABLE_ONLINE_API: "true"
      BOUNCER_KEY_megoopm: ${CROWDSEC_BOUNCER_KEY:?CROWDSEC_BOUNCER_KEY is required}
      COLLECTIONS: "crowdsecurity/appsec-virtual-patching crowdsecurity/appsec-generic-rules crowdsecurity/nginx"
    expose:
      - "8080"
      - "7422"
    volumes:
      - crowdsec_config:/etc/crowdsec
      - crowdsec_data:/var/lib/crowdsec/data
      - ./infra/crowdsec/acquis/appsec.yaml:/etc/crowdsec/acquis.d/appsec.yaml:ro

volumes:
  pgdata:
  app_data:
  crowdsec_config:
  crowdsec_data:
```

- [ ] **Step 2: Rewrite `.env.example`**

```dotenv
# MegooPM environment — read by docker compose from the repo root.
#
#   cp .env.example .env
#
# Used by BOTH docker-compose.yml (production, single node) and
# docker-compose.dev.yml (development). Development needs nothing set — every
# variable has a safe dev default baked into docker-compose.dev.yml. Production
# refuses to start until the "required" section is filled in.
# Multi-node clusters use .env.ha.example instead.

# ============ Required for production (docker-compose.yml) ============
# Generate secrets with:  openssl rand -hex 32
SECRET_KEY=
POSTGRES_PASSWORD=
# Shared by CrowdSec (registers it), the nginx bouncer and the backend.
CROWDSEC_BOUNCER_KEY=
# Shared by the worker and the nginx reload agent (docs/nginx-engine.md).
NGINX_RELOAD_TOKEN=
# Public URL the browser uses to reach the API (inlined into the UI at build
# time — run `docker compose build frontend` after changing it).
NEXT_PUBLIC_API_BASE_URL=
# Origin the browser loads the UI from (CORS allow-list).
CORS_ORIGINS=http://localhost:3000

# ============ Optional ============
POSTGRES_USER=megoopm
POSTGRES_DB=megoopm
# Initial admin, seeded only while the users table is empty. Production ships
# no default; alternatively seed later with `python -m scripts.create_user`.
FIRST_ADMIN_EMAIL=
FIRST_ADMIN_PASSWORD=
# Require login in the UI (production default true; dev default false).
NEXT_PUBLIC_AUTH_ENABLED=true

# ---------- Ports (host side) ----------
NGINX_HTTP_PORT=80
NGINX_HTTPS_PORT=443
FRONTEND_PORT=3000
BACKEND_PORT=8000
# Dev only (published by docker-compose.dev.yml):
POSTGRES_PORT=5432
REDIS_PORT=6379

# ---------- TLS / ACME ----------
# Production default is the real Let's Encrypt directory; dev defaults to
# staging. Uncomment to override either.
# ACME_DIRECTORY_URL=https://acme-staging-v02.api.letsencrypt.org/directory
ACME_ACCOUNT_EMAIL=
# DNS-01: wait for the _acme-challenge TXT record on every authoritative
# nameserver, then an extra settle period (anycast DNS needs a moment).
ACME_DNS_PROPAGATION_TIMEOUT_SECONDS=120
ACME_DNS_PROPAGATION_INTERVAL_SECONDS=5
ACME_DNS_PROPAGATION_SETTLE_SECONDS=10

# ---------- CrowdSec (docs/crowdsec.md) ----------
# Machine credentials are DB-backed and auto-registered; these are an optional
# one-time bootstrap seed.
CROWDSEC_MACHINE_ID=
CROWDSEC_MACHINE_PASSWORD=

# ---------- Development overrides (docker-compose.dev.yml) ----------
# ENVIRONMENT=development
# DEBUG=true
```

- [ ] **Step 3: Validate — required vars enforced, then a full config**

```bash
cd /c/Projects/MegooPM
docker compose -f docker-compose.yml --env-file .env.example config -q; echo "exit=$?"     # must FAIL (blank required vars)
cat > .env.prod.local <<'EOF'
SECRET_KEY=prodsecretprodsecretprodsecret12
POSTGRES_PASSWORD=prodpw
CROWDSEC_BOUNCER_KEY=prodbouncerkey
NGINX_RELOAD_TOKEN=prodreloadtoken
NEXT_PUBLIC_API_BASE_URL=http://localhost:18000
CORS_ORIGINS=http://localhost:13000
FIRST_ADMIN_EMAIL=admin@example.com
FIRST_ADMIN_PASSWORD=changeme
NGINX_HTTP_PORT=18080
NGINX_HTTPS_PORT=18443
FRONTEND_PORT=13000
BACKEND_PORT=18000
EOF
docker compose -f docker-compose.yml --env-file .env.prod.local config -q && echo "prod compose ok"
```

Expected: first command prints `required variable SECRET_KEY is missing a value: …` and `exit=1`; second prints `prod compose ok`. (`.env.prod.local` is gitignored via `.env.*.local`.)

- [ ] **Step 4: Boot the production stack on alternate ports and exercise it**

```bash
docker compose -f docker-compose.yml --env-file .env.prod.local up -d --build
sleep 60; docker compose -f docker-compose.yml --env-file .env.prod.local ps
curl -s -o /dev/null -w "ui %{http_code}\n" http://localhost:13000/login
curl -s http://localhost:18000/health
TOKEN=$(curl -s -X POST http://localhost:18000/api/v1/auth/login -H 'Content-Type: application/json' -d '{"email":"admin@example.com","password":"changeme"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
P=$(curl -s -X POST http://localhost:18000/api/v1/upstreams -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"name":"p","backends":[{"host":"10.0.0.1","port":8080}]}' | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
curl -s -X POST http://localhost:18000/api/v1/proxy-hosts -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d "{\"domain_names\":[\"prod.example.com\"],\"upstream_id\":$P}" | head -c 120; echo
sleep 5; docker compose -f docker-compose.yml --env-file .env.prod.local logs --since 60s worker | grep -iE "reload_nginx_config.*succeeded|valid" | tail -2
curl -s --max-time 90 -H "Host: prod.example.com" -o /dev/null -w "proxied host answers %{http_code}\n" http://localhost:18080/
docker compose -f docker-compose.yml --env-file .env.prod.local exec worker sh -c 'ls /var/run/docker.sock 2>&1 | head -1'
```

Expected: all services healthy (frontend via its image HEALTHCHECK); `ui 200`; health JSON; the worker log shows the reload task succeeded with `'valid': True, 'reloaded': True`; the proxied host answers `502` or `504` (upstream 10.0.0.1 is fake and nginx gives up after its 60 s connect timeout — the point is that nginx routed it via the managed vhost, not the default server's `404`); no docker.sock in the worker.

Tear down when done (this is a throwaway stack): `docker compose -f docker-compose.yml --env-file .env.prod.local down -v`.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat(compose): production single-node stack with hardened env" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: `docker-compose.ha.yml` — per-node production, `.env.ha.example`, HAProxy example

**Files:**
- Modify: `docker-compose.ha.yml` (rewrite)
- Create: `.env.ha.example`
- Modify: `infra/ha/haproxy.cfg` (rewrite)

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: project `megoopm-ha`; always-on services `data-init`, `backend`, `worker`, `nginx`, `frontend`; profile `control-plane` → `db`, `redis`, `crowdsec`; profile `scheduler` → `beat`. Bind mount `${SHARED_DATA_PATH}:/data`.

- [ ] **Step 1: Write the HA compose file**

`docker-compose.ha.yml`:

```yaml
# MegooPM — PRODUCTION, multi-node: run this file ON EVERY NODE.
#
#   cp .env.ha.example .env      # per node: NODE_ID, SHARED_DATA_PATH, secrets, URLs
#   docker compose -f docker-compose.ha.yml up -d --build      # or: make ha-up
#
# Every node runs the stateless app tier (API, Celery worker, managed nginx,
# web UI) against ONE shared data root mounted from the host at
# SHARED_DATA_PATH (NFS or another shared filesystem — docs/ha.md §2/§6) and
# shared Postgres/Redis/CrowdSec. Per-node profiles, chosen with
# COMPOSE_PROFILES in that node's .env:
#
#   control-plane   also run Postgres, Redis and CrowdSec LAPI here and publish
#                   them on the host (small clusters; otherwise use managed /
#                   external services and point the *_URL variables at them).
#   scheduler       run the single Celery beat here (sweeps are leader-locked,
#                   so an accidental second beat is harmless but pointless).
#
# Migrations: set RUN_MIGRATIONS=1 on the node you upgrade first, 0 elsewhere.
# No load balancer inside: put yours in front of :80/:443 (and :3000/:8000 if
# the admin surface should be balanced too) — infra/ha/haproxy.cfg is an example.

name: megoopm-ha

x-app-env: &app-env
  ENVIRONMENT: production
  DEBUG: "false"
  SECRET_KEY: ${SECRET_KEY:?SECRET_KEY is required and must be IDENTICAL on every node}
  DATABASE_URL: ${DATABASE_URL:?DATABASE_URL is required (shared Postgres)}
  REDIS_URL: ${REDIS_URL:?REDIS_URL is required (shared Redis)}
  CORS_ORIGINS: ${CORS_ORIGINS:-http://localhost:3000}
  # --- HA (docs/ha.md) ---
  HA_ENABLED: "true"
  NODE_ID: ${NODE_ID:?NODE_ID is required and must be unique per node}
  SHARED_DATA_DIR: /data
  # Node-local (NOT on the shared mount): run dir + reload marker.
  HA_LOCK_DIR: /var/run/megoopm
  NGINX_RELOAD_MARKER_PATH: /var/run/megoopm/nginx-config.version
  HA_RECONCILE_INTERVAL_SECONDS: ${HA_RECONCILE_INTERVAL_SECONDS:-15}
  # Worker -> LOCAL nginx via the in-container reload agent.
  NGINX_TEST_COMMAND: "python -m scripts.nginx_remote test"
  NGINX_RELOAD_COMMAND: "python -m scripts.nginx_remote reload"
  NGINX_AGENT_ADDR: nginx:9099
  NGINX_RELOAD_TOKEN: ${NGINX_RELOAD_TOKEN:?NGINX_RELOAD_TOKEN is required}
  ACME_DIRECTORY_URL: ${ACME_DIRECTORY_URL:-https://acme-v02.api.letsencrypt.org/directory}
  ACME_ACCOUNT_EMAIL: ${ACME_ACCOUNT_EMAIL:-}
  ACME_DNS_PROPAGATION_TIMEOUT_SECONDS: ${ACME_DNS_PROPAGATION_TIMEOUT_SECONDS:-120}
  ACME_DNS_PROPAGATION_INTERVAL_SECONDS: ${ACME_DNS_PROPAGATION_INTERVAL_SECONDS:-5}
  ACME_DNS_PROPAGATION_SETTLE_SECONDS: ${ACME_DNS_PROPAGATION_SETTLE_SECONDS:-10}
  CROWDSEC_LAPI_URL: ${CROWDSEC_LAPI_URL:?CROWDSEC_LAPI_URL is required (shared LAPI)}
  CROWDSEC_LAPI_KEY: ${CROWDSEC_BOUNCER_KEY:?CROWDSEC_BOUNCER_KEY is required}
  FIRST_ADMIN_EMAIL: ${FIRST_ADMIN_EMAIL:-}
  FIRST_ADMIN_PASSWORD: ${FIRST_ADMIN_PASSWORD:-}

services:
  # One-shot: lay out the shared root and hand it to uid 1000. Fails fast when
  # the mount is not writable by root/uid 1000 (e.g. NFS root_squash without a
  # matching anonuid) instead of letting the backend hit PermissionError later.
  data-init:
    image: busybox:1.36
    command: >
      sh -c "mkdir -p /data/nginx/conf.d/stream /data/certs/_acme-challenge
             && chown -R 1000:1000 /data
             || { echo 'data-init: cannot prepare /data (SHARED_DATA_PATH) for uid 1000' >&2; exit 1; }"
    volumes:
      - ${SHARED_DATA_PATH:?SHARED_DATA_PATH is required (host path of the shared mount)}:/data

  backend:
    build: ./backend
    restart: unless-stopped
    environment:
      <<: *app-env
      RUN_MIGRATIONS: ${RUN_MIGRATIONS:-0}
    ports:
      - "${BACKEND_PORT:-8000}:8000"
    volumes:
      - ${SHARED_DATA_PATH:?}:/data
      - /var/run/megoopm
    depends_on:
      data-init:
        condition: service_completed_successfully
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/health"]
      interval: 15s
      timeout: 5s
      start_period: 30s
      retries: 5

  worker:
    build: ./backend
    restart: unless-stopped
    command: ["celery", "-A", "app.core.celery_app.celery_app", "worker", "--loglevel=info"]
    environment:
      <<: *app-env
      RUN_MIGRATIONS: "0"
    volumes:
      - ${SHARED_DATA_PATH:?}:/data
      - /var/run/megoopm
    depends_on:
      backend:
        condition: service_healthy
      nginx:
        condition: service_started
    healthcheck:
      test: ["CMD", "celery", "-A", "app.core.celery_app.celery_app", "inspect", "ping"]
      interval: 20s
      timeout: 10s
      start_period: 25s
      retries: 5

  nginx:
    build: ./infra/nginx
    restart: unless-stopped
    environment:
      CROWDSEC_LAPI_URL: ${CROWDSEC_LAPI_URL:?}
      CROWDSEC_APPSEC_URL: ${CROWDSEC_APPSEC_URL:?CROWDSEC_APPSEC_URL is required (shared AppSec)}
      CROWDSEC_BOUNCER_KEY: ${CROWDSEC_BOUNCER_KEY:?}
      NGINX_RELOAD_TOKEN: ${NGINX_RELOAD_TOKEN:?}
    ports:
      - "${NGINX_HTTP_PORT:-80}:80"
      - "${NGINX_HTTPS_PORT:-443}:443"
    volumes:
      - ./infra/nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ${SHARED_DATA_PATH:?}:/data
    depends_on:
      data-init:
        condition: service_completed_successfully

  frontend:
    build:
      context: ./frontend
      target: runner
      args:
        NEXT_PUBLIC_API_BASE_URL: ${NEXT_PUBLIC_API_BASE_URL:?NEXT_PUBLIC_API_BASE_URL is required}
        NEXT_PUBLIC_AUTH_ENABLED: ${NEXT_PUBLIC_AUTH_ENABLED:-true}
    restart: unless-stopped
    ports:
      - "${FRONTEND_PORT:-3000}:3000"

  # ---------------- profile: scheduler (exactly one node) ----------------
  beat:
    profiles: ["scheduler"]
    build: ./backend
    restart: unless-stopped
    command: ["celery", "-A", "app.core.celery_app.celery_app", "beat", "--loglevel=info"]
    environment:
      <<: *app-env
      RUN_MIGRATIONS: "0"
    healthcheck:
      disable: true

  # ---------------- profile: control-plane (one node, small clusters) ----
  db:
    profiles: ["control-plane"]
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-megoopm}
      # Empty makes the postgres image refuse to initialise — set it on the
      # control-plane node's .env (and use the same value in DATABASE_URL).
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-}
      POSTGRES_DB: ${POSTGRES_DB:-megoopm}
    ports:
      - "${CONTROL_PLANE_BIND:-0.0.0.0}:${POSTGRES_PORT:-5432}:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-megoopm} -d ${POSTGRES_DB:-megoopm}"]
      interval: 5s
      timeout: 5s
      retries: 10

  redis:
    profiles: ["control-plane"]
    image: redis:7-alpine
    restart: unless-stopped
    ports:
      - "${CONTROL_PLANE_BIND:-0.0.0.0}:${REDIS_PORT:-6379}:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  crowdsec:
    profiles: ["control-plane"]
    image: crowdsecurity/crowdsec:v1.6.4
    restart: unless-stopped
    environment:
      DISABLE_ONLINE_API: "true"
      BOUNCER_KEY_megoopm: ${CROWDSEC_BOUNCER_KEY:?}
      COLLECTIONS: "crowdsecurity/appsec-virtual-patching crowdsecurity/appsec-generic-rules crowdsecurity/nginx"
    ports:
      - "${CONTROL_PLANE_BIND:-0.0.0.0}:${CROWDSEC_LAPI_PORT:-8080}:8080"
      - "${CONTROL_PLANE_BIND:-0.0.0.0}:${CROWDSEC_APPSEC_PORT:-7422}:7422"
    volumes:
      - crowdsec_config:/etc/crowdsec
      - crowdsec_data:/var/lib/crowdsec/data
      - ./infra/crowdsec/acquis/appsec.yaml:/etc/crowdsec/acquis.d/appsec.yaml:ro

volumes:
  # Control-plane node only (local state of the shared services).
  pgdata:
  crowdsec_config:
  crowdsec_data:
```

Note on ordering: `backend` has no `depends_on` for `db`/`redis` because they may be external (and depending on a profile-gated service would drag the profile in). If the shared database is not yet reachable, the entrypoint's `alembic upgrade head` fails and `restart: unless-stopped` retries — expected and documented.

- [ ] **Step 2: Write `.env.ha.example`**

```dotenv
# MegooPM — per-NODE environment for docker-compose.ha.yml.
#
#   cp .env.ha.example .env        # on every node, then edit the node-specific lines
#
# Values marked IDENTICAL must be the same on every node; the rest are per node.

# ---------- This node ----------
# Unique, stable identifier (stamped on config-version bumps; docs/ha.md §4).
NODE_ID=node-a
# Host directory of the shared mount (NFS etc.) — mounted into the containers
# at /data. Must be writable by uid 1000 on every node (docs/ha.md §6).
SHARED_DATA_PATH=/mnt/megoopm
# Profiles this node runs (comma-separated): control-plane, scheduler, or none.
# Exactly one node should run `scheduler`; `control-plane` hosts Postgres,
# Redis and CrowdSec for small clusters (leave empty when they are external).
COMPOSE_PROFILES=control-plane,scheduler
# 1 on the node you upgrade first (applies migrations), 0 on the others.
RUN_MIGRATIONS=1
# Interface the control-plane services are published on (control-plane node only).
CONTROL_PLANE_BIND=0.0.0.0

# ---------- Shared services (IDENTICAL on every node) ----------
# On the control-plane node the hostnames are the compose service names
# (db, redis, crowdsec); on every other node use that node's address.
DATABASE_URL=postgresql+asyncpg://megoopm:CHANGE_ME@db:5432/megoopm
REDIS_URL=redis://redis:6379/0
CROWDSEC_LAPI_URL=http://crowdsec:8080
CROWDSEC_APPSEC_URL=http://crowdsec:7422
# Control-plane node only: what the local Postgres is initialised with (must
# match DATABASE_URL above).
POSTGRES_USER=megoopm
POSTGRES_PASSWORD=CHANGE_ME
POSTGRES_DB=megoopm

# ---------- Secrets (IDENTICAL on every node; openssl rand -hex 32) ----------
SECRET_KEY=
CROWDSEC_BOUNCER_KEY=
NGINX_RELOAD_TOKEN=

# ---------- Admin surface ----------
# Public URL of the API as the browser reaches it (via your LB); inlined into
# the UI at build time — `docker compose -f docker-compose.ha.yml build frontend`
# after changing it.
NEXT_PUBLIC_API_BASE_URL=https://megoopm-api.example.com
NEXT_PUBLIC_AUTH_ENABLED=true
CORS_ORIGINS=https://megoopm.example.com
FIRST_ADMIN_EMAIL=
FIRST_ADMIN_PASSWORD=

# ---------- Ports on this host ----------
NGINX_HTTP_PORT=80
NGINX_HTTPS_PORT=443
FRONTEND_PORT=3000
BACKEND_PORT=8000
# control-plane node only
POSTGRES_PORT=5432
REDIS_PORT=6379
CROWDSEC_LAPI_PORT=8080
CROWDSEC_APPSEC_PORT=7422

# ---------- TLS / ACME ----------
# ACME_DIRECTORY_URL=https://acme-staging-v02.api.letsencrypt.org/directory
ACME_ACCOUNT_EMAIL=
ACME_DNS_PROPAGATION_TIMEOUT_SECONDS=120
ACME_DNS_PROPAGATION_INTERVAL_SECONDS=5
ACME_DNS_PROPAGATION_SETTLE_SECONDS=10
# Backstop reconcile cadence per node (seconds).
HA_RECONCILE_INTERVAL_SECONDS=15
```

- [ ] **Step 3: Rewrite the HAProxy example for an external LB**

`infra/ha/haproxy.cfg`:

```
# Example EXTERNAL load balancer for a MegooPM cluster (docker-compose.ha.yml).
# Runs on its own host (or as a keepalived-backed pair) — NOT inside the stack.
# Replace the node addresses below with your nodes'. TCP passthrough on
# :80/:443 keeps the LB cert-agnostic: each node's nginx terminates TLS with
# the certificates on the shared mount. :3000/:8000 balance the admin UI/API.

global
    log stdout format raw local0
    maxconn 4096

defaults
    log     global
    timeout connect 5s
    timeout client  60s
    timeout server  60s

# --- Proxied traffic: L4 passthrough to every node's managed nginx ---
frontend http_in
    mode tcp
    bind *:80
    default_backend nginx_nodes_http

backend nginx_nodes_http
    mode tcp
    balance roundrobin
    server node-a 10.0.0.11:80 check
    server node-b 10.0.0.12:80 check

frontend https_in
    mode tcp
    bind *:443
    default_backend nginx_nodes_https

backend nginx_nodes_https
    mode tcp
    balance roundrobin
    server node-a 10.0.0.11:443 check
    server node-b 10.0.0.12:443 check

# --- Admin surface: L7 across the stateless API and UI ---
frontend api_in
    mode http
    bind *:8000
    default_backend api_nodes

backend api_nodes
    mode http
    balance roundrobin
    option httpchk GET /health
    server node-a 10.0.0.11:8000 check
    server node-b 10.0.0.12:8000 check

frontend ui_in
    mode http
    bind *:3000
    default_backend ui_nodes

backend ui_nodes
    mode http
    balance roundrobin
    option httpchk GET /login
    server node-a 10.0.0.11:3000 check
    server node-b 10.0.0.12:3000 check
```

- [ ] **Step 4: Validate the file**

```bash
cd /c/Projects/MegooPM
docker compose -f docker-compose.ha.yml --env-file .env.ha.example config -q; echo "exit=$?"   # FAILS: SECRET_KEY blank
mkdir -p /c/megoopm-ha-data
cat > .env.ha.local <<'EOF'
NODE_ID=node-a
SHARED_DATA_PATH=C:/megoopm-ha-data
COMPOSE_PROFILES=control-plane,scheduler
RUN_MIGRATIONS=1
DATABASE_URL=postgresql+asyncpg://megoopm:hapw@db:5432/megoopm
REDIS_URL=redis://redis:6379/0
CROWDSEC_LAPI_URL=http://crowdsec:8080
CROWDSEC_APPSEC_URL=http://crowdsec:7422
POSTGRES_PASSWORD=hapw
SECRET_KEY=hasecrethasecrethasecrethasecret
CROWDSEC_BOUNCER_KEY=habouncer
NGINX_RELOAD_TOKEN=hareload
NEXT_PUBLIC_API_BASE_URL=http://localhost:28000
CORS_ORIGINS=http://localhost:23000
FIRST_ADMIN_EMAIL=admin@example.com
FIRST_ADMIN_PASSWORD=changeme
NGINX_HTTP_PORT=28080
NGINX_HTTPS_PORT=28443
FRONTEND_PORT=23000
BACKEND_PORT=28000
POSTGRES_PORT=25432
REDIS_PORT=26379
CROWDSEC_LAPI_PORT=28081
CROWDSEC_APPSEC_PORT=27422
EOF
docker compose -f docker-compose.ha.yml --env-file .env.ha.local config --services
```

Expected: first `exit=1` naming `SECRET_KEY`; the second lists `data-init backend worker nginx frontend beat db redis crowdsec` (profiles active via the env file).

- [ ] **Step 5: Boot one HA node locally and prove node identity + shared path**

```bash
MSYS_NO_PATHCONV=1 docker compose -f docker-compose.ha.yml --env-file .env.ha.local up -d --build
sleep 75; docker compose -f docker-compose.ha.yml --env-file .env.ha.local ps
TOKEN=$(curl -s -X POST http://localhost:28000/api/v1/auth/login -H 'Content-Type: application/json' -d '{"email":"admin@example.com","password":"changeme"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
P=$(curl -s -X POST http://localhost:28000/api/v1/upstreams -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"name":"p","backends":[{"host":"10.0.0.1","port":8080}]}' | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
curl -s -o /dev/null -w "host create %{http_code}\n" -X POST http://localhost:28000/api/v1/proxy-hosts -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d "{\"domain_names\":[\"ha.example.com\"],\"upstream_id\":$P}"
sleep 8
docker compose -f docker-compose.ha.yml --env-file .env.ha.local exec db psql -U megoopm -d megoopm -At -c "select config_version, updated_by from cluster_state;"
ls /c/megoopm-ha-data/nginx/conf.d/
docker compose -f docker-compose.ha.yml --env-file .env.ha.local exec worker cat /var/run/megoopm/nginx-config.version
docker compose -f docker-compose.ha.yml --env-file .env.ha.local logs --since 2m worker | grep -iE "reconcile_local_nginx.*succeeded" | tail -1
```

Expected: services healthy; `host create 201`; `cluster_state` shows a version ≥ 1 with `updated_by = node-a`; `megoopm-proxy-<id>.conf` present under the **host** path; the node-local marker equals the shared version; a `reconcile_local_nginx` task succeeded (broadcast fan-out). On Windows/Docker Desktop the `chown` in `data-init` is a silent no-op on the drive bind mount (the files are world-writable), which is fine for this check.

Tear down: `MSYS_NO_PATHCONV=1 docker compose -f docker-compose.ha.yml --env-file .env.ha.local down -v` and `rm -rf /c/megoopm-ha-data`.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.ha.yml .env.ha.example infra/ha/haproxy.cfg
git commit -m "feat(compose): per-node HA stack with node id, shared data path and profiles" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Compose config smoke test

**Files:**
- Create: `backend/tests/test_compose_config.py`

**Interfaces:**
- Consumes: the three compose files and `.env.ha.example` (Tasks 4–6).

- [ ] **Step 1: Write the test**

```python
"""``docker compose config`` must succeed for every shipped compose file.

Runs only where the ``docker`` CLI is available (CI runners have it; the
backend test container does not) — otherwise the module is skipped, never
silently green.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_PROD = {
    "SECRET_KEY": "x" * 32,
    "POSTGRES_PASSWORD": "pw",
    "CROWDSEC_BOUNCER_KEY": "bouncer",
    "NGINX_RELOAD_TOKEN": "token",
    "NEXT_PUBLIC_API_BASE_URL": "http://localhost:8000",
}
REQUIRED_HA = {
    "NODE_ID": "node-test",
    "SHARED_DATA_PATH": "/tmp/megoopm-shared",
    "DATABASE_URL": "postgresql+asyncpg://u:p@db:5432/megoopm",
    "REDIS_URL": "redis://redis:6379/0",
    "CROWDSEC_LAPI_URL": "http://crowdsec:8080",
    "CROWDSEC_APPSEC_URL": "http://crowdsec:7422",
    "SECRET_KEY": "x" * 32,
    "CROWDSEC_BOUNCER_KEY": "bouncer",
    "NGINX_RELOAD_TOKEN": "token",
    "NEXT_PUBLIC_API_BASE_URL": "http://localhost:8000",
    "COMPOSE_PROFILES": "control-plane,scheduler",
}

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available")


def _config(compose_file: str, env: dict[str, str], tmp_path: Path) -> subprocess.CompletedProcess:
    env_file = tmp_path / ".env"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8")
    return subprocess.run(
        ["docker", "compose", "-f", compose_file, "--env-file", str(env_file), "config", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize(
    ("compose_file", "env"),
    [
        ("docker-compose.dev.yml", {}),
        ("docker-compose.yml", REQUIRED_PROD),
        ("docker-compose.ha.yml", REQUIRED_HA),
    ],
)
def test_compose_file_is_valid(compose_file: str, env: dict[str, str], tmp_path: Path) -> None:
    result = _config(compose_file, env, tmp_path)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("compose_file", "env", "missing"),
    [
        ("docker-compose.yml", REQUIRED_PROD, "NGINX_RELOAD_TOKEN"),
        ("docker-compose.ha.yml", REQUIRED_HA, "NODE_ID"),
        ("docker-compose.ha.yml", REQUIRED_HA, "SHARED_DATA_PATH"),
    ],
)
def test_production_files_refuse_missing_required_vars(
    compose_file: str, env: dict[str, str], missing: str, tmp_path: Path
) -> None:
    result = _config(compose_file, {k: v for k, v in env.items() if k != missing}, tmp_path)
    assert result.returncode != 0
    assert missing in result.stderr
```

- [ ] **Step 2: Run it where docker exists (the host cannot collect backend tests, so run just this module with the repo's Python)**

Run (from `backend/`, PowerShell/Git Bash on the host): `uv run --extra dev python -m pytest -q -p no:warnings tests/test_compose_config.py -p no:cacheprovider --noconftest`
Expected: PASS (6 tests). (`--noconftest` skips the app-importing conftest that breaks on Windows; this module does not need it.) Remove the `backend/uv.lock` that `uv run` may create: `rm -f backend/uv.lock`.

Also run in the test container to confirm the skip: `docker exec megoopm-test python -m pytest -q -p no:warnings tests/test_compose_config.py` → `6 skipped`.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_compose_config.py
git commit -m "test(compose): validate all compose files and their required env" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Documentation

**Files:**
- Modify: `README.md`, `docs/CONVENTIONS.md:117-140`, `docs/ha.md` (§2, §6, §7, §9), `docs/nginx-engine.md:86-160`, `backend/.env.example:41-51`

- [ ] **Step 1: README**

- Repository layout block: replace the `docker-compose.yml` line with three lines:
  ```
  ├── docker-compose.yml       Production, single node
  ├── docker-compose.dev.yml   Development with hot reload
  ├── docker-compose.ha.yml    Production, one file per node of a cluster
  ```
- Quick start: `docker compose up --build` → `docker compose -f docker-compose.dev.yml up --build` and `# or: make up (detached) / make up-fg (foreground)`.
- Replace the paragraph starting "The frontend reaches the backend at `NEXT_PUBLIC_API_BASE_URL`" with:
  ```markdown
  The frontend reaches the backend at `NEXT_PUBLIC_API_BASE_URL` (default
  `http://localhost:8000`). The backend writes managed vhosts and TLS certs under
  `/data` (shared with the nginx container), and the worker validates/reloads
  nginx through a token-gated agent inside the nginx container — no Docker
  socket anywhere. Edits under `backend/` and `frontend/` hot-reload. `make help`
  lists the common tasks. See [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md#local-dev-orchestration).

  ## Deploying

  | Topology | File | Env template | Start |
  | --- | --- | --- | --- |
  | Single node | `docker-compose.yml` | `.env.example` → `.env` | `docker compose up -d --build` (`make prod-up`) |
  | Multi-node (run on every node) | `docker-compose.ha.yml` | `.env.ha.example` → `.env` | `docker compose -f docker-compose.ha.yml up -d --build` (`make ha-up`) |

  Production refuses to start until the required secrets in the template are
  set. `NEXT_PUBLIC_*` values are baked into the UI image at build time — rebuild
  the frontend after changing them. Multi-node specifics (shared storage path,
  node ids, the `control-plane` / `scheduler` profiles, the external load
  balancer) are in [`docs/ha.md`](docs/ha.md).
  ```

- [ ] **Step 2: CONVENTIONS.md local-dev section**

Replace the two bullets about `docker-compose.yml` and the managed-proxy contract with:

```markdown
- **`docker-compose.dev.yml` (root)** is the development full stack: `db`,
  `redis`, `backend` (uvicorn `--reload`), `worker` (Celery under `watchfiles`)
  + `beat`, `frontend` (`next dev`), `nginx` (the managed proxy) and `crowdsec`.
  `backend/` and `frontend/` are bind-mounted, so edits hot-reload.
  `docker-compose.yml` is the production single-node stack and
  `docker-compose.ha.yml` the per-node cluster stack — see `docs/ha.md`.
- **Managed proxy contract:** the backend writes vhosts to `/data/nginx/conf.d`
  (streams to `/data/nginx/conf.d/stream`) and TLS certs to `/data/certs`; the
  nginx container mounts the same paths and `infra/nginx/nginx.conf` `include`s
  them. A one-shot `data-init` service chowns `/data` to the backend user
  (uid 1000). The worker validates/reloads nginx through the reload agent in the
  nginx container (`python -m scripts.nginx_remote`, `docs/nginx-engine.md`),
  never through the Docker socket.
```

Keep the `.env` bullet; change "`docker compose up`" in the quick-start code block to `docker compose -f docker-compose.dev.yml up --build`.

- [ ] **Step 3: ha.md**

- §2 table: add a first row `| `SHARED_DATA_PATH` (host, compose only) | e.g. `/mnt/megoopm` | The host directory bind-mounted to `/data` in every container |`.
- §6 "Reference deployment": replace everything from the heading to just before `### NFS-backed volume` with:

  ```markdown
  ## 6. Reference deployment (per-node compose)

  `docker-compose.ha.yml` is run **on every node** with that node's `.env`
  (template: `.env.ha.example`):

  ```bash
  cp .env.ha.example .env      # set NODE_ID, SHARED_DATA_PATH, secrets, shared URLs
  docker compose -f docker-compose.ha.yml up -d --build
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
  ```
- `### NFS-backed volume` subsection: keep the mount-option guidance, but replace the compose `volumes:` example with: "Mount the NFS export on the host (e.g. at `/mnt/megoopm`, `fstab` with `nfsvers=4.1,hard,noatime`) and set `SHARED_DATA_PATH=/mnt/megoopm`; compose bind-mounts it."
- §7: replace the "Add a node" paragraph with: "**Add a node:** mount the shared export at the same host path, copy `.env.ha.example` to `.env` with a new `NODE_ID`, the shared `*_URL`s and the identical secrets, no profiles, `RUN_MIGRATIONS=0`; `docker compose -f docker-compose.ha.yml up -d --build`; register it with the LB. …" (keep the rest).
- §9 table: add rows `SHARED_DATA_PATH` (compose; host dir → `/data`), `NGINX_RELOAD_TOKEN` (shared secret for the reload agent), `NGINX_AGENT_ADDR` (`nginx:9099`), `COMPOSE_PROFILES` (`control-plane`, `scheduler`).

- [ ] **Step 4: nginx-engine.md**

Replace the "Resolved per topology" table and the whole "Production transport" section (through the "Rejected: …" paragraph and the "Until the production channel is wired…" paragraph) with:

```markdown
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
`__MEGOOPM_STATUS__ <exit code>`. The worker side is
`python -m scripts.nginx_remote test|reload` (env `NGINX_AGENT_ADDR`,
`NGINX_RELOAD_TOKEN`), which mirrors the output to stderr and exits with the
remote status — so `NGINX_TEST_COMMAND`/`NGINX_RELOAD_COMMAND` point at it and
the engine's validate → reload → rollback logic is unchanged. Same binary, same
modules, same container: `-t` can never diverge from what reloads. The
container healthcheck also `ping`s the agent.

Rejected alternatives: `docker exec` over the daemon socket (container-escape
grade privilege; removed even from dev); a PID-namespace sidecar running the
commands (`-s reload` reads `nginx.pid` from the sidecar's own filesystem, where
it does not exist); shipping the openresty binary into the worker image (module
drift makes `-t` on the worker untrustworthy).
```

- [ ] **Step 5: backend/.env.example**

Replace lines 45–46 (`NGINX_CONFD_DIR=/etc/nginx/conf.d` / `NGINX_CERTS_DIR=/etc/nginx/certs`) with comments `# NGINX_CONFD_DIR=/data/nginx/conf.d` / `# NGINX_CERTS_DIR=/data/certs` (derived from `SHARED_DATA_DIR` when unset), and after the `NGINX_RELOAD_COMMAND` line add:

```dotenv
# In the compose stacks both commands are `python -m scripts.nginx_remote test|reload`,
# which talks to the reload agent in the nginx container:
# NGINX_AGENT_ADDR=nginx:9099
# NGINX_RELOAD_TOKEN=
```

- [ ] **Step 6: Commit**

```bash
git add README.md docs/CONVENTIONS.md docs/ha.md docs/nginx-engine.md backend/.env.example
git commit -m "docs: deployment topologies, reload agent, per-node HA" -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Final gates

- [ ] **Step 1: Backend**

```bash
docker exec megoopm-test python -m pytest -q -p no:warnings
docker exec megoopm-test python -m ruff check .
```
Expected: all pass (the two `test_dns01_pg.py` `cf`-name collisions are a known dev-DB artefact; they pass against a fresh database), ruff clean.

- [ ] **Step 2: Frontend**

Run (in `frontend/`): `npm run lint && npm run typecheck && npm test`

- [ ] **Step 3: Dev stack still healthy after everything**

`docker compose -f docker-compose.dev.yml ps` — all healthy; `git status` clean; remove `.env.prod.local`, `.env.ha.local` and the test container (`docker rm -f megoopm-test`).
