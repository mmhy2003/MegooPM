"""Restart the CrowdSec container so it re-reads its parser files.

CrowdSec loads parsers at startup, exposes no reload endpoint, and LAPI has no
route for parser configuration, so a restart is the only channel available. We
talk to the docker daemon over its unix socket with ``httpx`` rather than
adding the docker SDK — ``httpx`` is already a dependency and one endpoint is
all this needs.

The socket is mounted on the **worker** only. It is root-equivalent on the host,
and the API process is the one taking internet traffic.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

# Pinned API version: the daemon serves any version it supports, and pinning
# keeps this path stable if the host's docker is upgraded under us.
_DOCKER_API = "v1.43"

# Seconds docker waits for a graceful stop before killing the container.
_STOP_GRACE_SECONDS = 10


class CrowdSecReloadError(RuntimeError):
    """The CrowdSec container could not be restarted."""


_EACCES_HINT = (
    " The worker cannot open the socket: it runs as uid 1000 and the socket is "
    "root:docker. Set DOCKER_GID to the socket's group id "
    "(stat -c %g /var/run/docker.sock) and recreate the worker."
)


def _connect_failure(where: str, verb: str, exc: httpx.HTTPError) -> CrowdSecReloadError:
    """One wording for every failure to reach the daemon, with the EACCES fix named.

    "Permission denied" on the socket has exactly one cause in this stack, and
    a bare errno would send the operator to the wrong place (the container
    name, the mount) first.
    """
    detail = str(exc) or "no detail"
    message = (
        f"Could not reach the docker daemon to {verb} {where}: {type(exc).__name__} — {detail}"
    )
    if "Permission denied" in detail or "Errno 13" in detail:
        message += _EACCES_HINT
    return CrowdSecReloadError(message)


def restart_container(
    name: str,
    *,
    socket_path: str,
    timeout_seconds: float,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """Restart ``name`` via the docker socket. Raises on anything but success.

    Every error names both the container and the socket. The two likely
    misconfigurations — a wrong ``CROWDSEC_CONTAINER_NAME`` and a socket that
    is unmounted or unreadable — are indistinguishable from the symptom alone,
    and a bare "restart failed" is what turns a five-minute fix into an
    afternoon.
    """
    client_transport = transport or httpx.HTTPTransport(uds=socket_path)
    where = f"container {name!r} via {socket_path}"
    try:
        with httpx.Client(
            transport=client_transport,
            base_url="http://docker",
            timeout=timeout_seconds,
        ) as client:
            resp = client.post(
                f"/{_DOCKER_API}/containers/{name}/restart",
                params={"t": _STOP_GRACE_SECONDS},
            )
    except httpx.HTTPError as exc:
        raise _connect_failure(where, "restart", exc) from exc

    # 204 = restarting; 304 = already in the requested state.
    if resp.status_code not in (httpx.codes.NO_CONTENT, httpx.codes.NOT_MODIFIED):
        raise CrowdSecReloadError(
            f"Docker refused to restart {where}: HTTP {resp.status_code} — "
            f"{resp.text.strip() or 'no body'}"
        )


@dataclass(frozen=True, slots=True)
class ExecResult:
    """What a command in the container produced."""

    exit_code: int
    output: str


def exec_in_container(
    name: str,
    argv: list[str],
    *,
    socket_path: str,
    timeout_seconds: float,
    transport: httpx.BaseTransport | None = None,
) -> ExecResult:
    """Run ``argv`` inside ``name`` and return its exit code and output.

    Three calls: create the exec, start it (with a TTY, so the stream is
    plain text rather than docker's multiplexed frames), inspect it for the
    exit code. A non-zero exit is a result, not an error: the caller reads
    the output. Errors are for the daemon being unreachable or refusing.
    """
    client_transport = transport or httpx.HTTPTransport(uds=socket_path)
    where = f"container {name!r} via {socket_path}"
    try:
        with httpx.Client(
            transport=client_transport, base_url="http://docker", timeout=timeout_seconds
        ) as client:
            created = client.post(
                f"/{_DOCKER_API}/containers/{name}/exec",
                json={"AttachStdout": True, "AttachStderr": True, "Tty": True, "Cmd": argv},
            )
            if created.status_code != httpx.codes.CREATED:
                raise CrowdSecReloadError(
                    f"Docker refused to exec in {where}: HTTP {created.status_code} — "
                    f"{created.text.strip() or 'no body'}"
                )
            exec_id = created.json()["Id"]
            started = client.post(
                f"/{_DOCKER_API}/exec/{exec_id}/start", json={"Detach": False, "Tty": True}
            )
            if started.status_code != httpx.codes.OK:
                raise CrowdSecReloadError(
                    f"Docker could not start the exec in {where}: HTTP {started.status_code}"
                )
            output = started.content.decode("utf-8", errors="replace")
            inspected = client.get(f"/{_DOCKER_API}/exec/{exec_id}/json")
            exit_code = int(inspected.json().get("ExitCode") or 0)
    except httpx.HTTPError as exc:
        raise _connect_failure(where, "exec in", exc) from exc
    return ExecResult(exit_code=exit_code, output=output)


__all__ = ["CrowdSecReloadError", "ExecResult", "exec_in_container", "restart_container"]
