"""docker exec over the socket, against a mock transport."""

from __future__ import annotations

import json

import httpx
import pytest
from app.services.crowdsec.reload import CrowdSecReloadError, exec_in_container


def _daemon(*, exit_code: int = 0, output: str = "hello\n") -> tuple[httpx.MockTransport, list]:
    seen: list[tuple[str, str, dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        if request.url.path.endswith("/containers/megoopm-crowdsec-1/exec"):
            return httpx.Response(201, json={"Id": "exec123"})
        if request.url.path.endswith("/exec/exec123/start"):
            return httpx.Response(200, content=output.encode())
        if request.url.path.endswith("/exec/exec123/json"):
            return httpx.Response(200, json={"ExitCode": exit_code, "Running": False})
        return httpx.Response(404, text="no such thing")

    return httpx.MockTransport(handler), seen


def test_create_start_inspect_and_the_output_comes_back() -> None:
    transport, seen = _daemon(output="ok\n")
    result = exec_in_container(
        "megoopm-crowdsec-1",
        ["cscli", "hub", "update"],
        socket_path="/var/run/docker.sock",
        timeout_seconds=30,
        transport=transport,
    )
    assert (result.exit_code, result.output) == (0, "ok\n")
    methods_paths = [(m, p.split("/v1.43")[-1]) for m, p, _ in seen]
    assert methods_paths == [
        ("POST", "/containers/megoopm-crowdsec-1/exec"),
        ("POST", "/exec/exec123/start"),
        ("GET", "/exec/exec123/json"),
    ]
    create_body = seen[0][2]
    # A TTY so the stream is plain text, not 8-byte multiplexed frames.
    assert create_body == {
        "AttachStdout": True,
        "AttachStderr": True,
        "Tty": True,
        "Cmd": ["cscli", "hub", "update"],
    }


def test_a_non_zero_exit_is_returned_not_raised() -> None:
    transport, _ = _daemon(exit_code=1, output="level=fatal msg=boom\n")
    result = exec_in_container(
        "megoopm-crowdsec-1",
        ["cscli", "x"],
        socket_path="/var/run/docker.sock",
        timeout_seconds=30,
        transport=transport,
    )
    assert result.exit_code == 1 and "boom" in result.output


def test_missing_container_names_what_it_tried() -> None:
    transport = httpx.MockTransport(lambda _r: httpx.Response(404, text="No such container"))
    with pytest.raises(CrowdSecReloadError) as exc:
        exec_in_container(
            "nope",
            ["true"],
            socket_path="/var/run/docker.sock",
            timeout_seconds=30,
            transport=transport,
        )
    assert "'nope'" in str(exc.value) and "/var/run/docker.sock" in str(exc.value)


def test_socket_error_names_the_socket_path() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no such file")

    with pytest.raises(CrowdSecReloadError) as exc:
        exec_in_container(
            "megoopm-crowdsec-1",
            ["true"],
            socket_path="/var/run/docker.sock",
            timeout_seconds=30,
            transport=httpx.MockTransport(handler),
        )
    assert "/var/run/docker.sock" in str(exc.value)
