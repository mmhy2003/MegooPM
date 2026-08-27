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
