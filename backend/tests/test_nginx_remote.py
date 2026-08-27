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
    a = agent(
        lambda line: b"nginx: [emerg] unknown directive\nnginx: test failed\n__MEGOOPM_STATUS__ 1\n"
    )
    assert nginx_remote.run("test", addr=a.addr, token="t0k", timeout=2) == 1
    err = capsys.readouterr().err
    assert "unknown directive" in err
    assert "__MEGOOPM_STATUS__" not in err


def test_missing_status_line_is_70(agent, capsys) -> None:
    a = agent(lambda line: b"connection dropped mid-way\n")
    assert (
        nginx_remote.run("test", addr=a.addr, token="t0k", timeout=2) == nginx_remote.EXIT_NO_STATUS
    )
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
