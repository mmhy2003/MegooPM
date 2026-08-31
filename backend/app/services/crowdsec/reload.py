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

import httpx

# Pinned API version: the daemon serves any version it supports, and pinning
# keeps this path stable if the host's docker is upgraded under us.
_DOCKER_API = "v1.43"

# Seconds docker waits for a graceful stop before killing the container.
_STOP_GRACE_SECONDS = 10


class CrowdSecReloadError(RuntimeError):
    """The CrowdSec container could not be restarted."""


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
        detail = str(exc) or "no detail"
        raise CrowdSecReloadError(
            f"Could not reach the docker daemon to restart {where}: "
            f"{type(exc).__name__} — {detail}"
        ) from exc

    # 204 = restarting; 304 = already in the requested state.
    if resp.status_code not in (httpx.codes.NO_CONTENT, httpx.codes.NOT_MODIFIED):
        raise CrowdSecReloadError(
            f"Docker refused to restart {where}: HTTP {resp.status_code} — "
            f"{resp.text.strip() or 'no body'}"
        )


__all__ = ["CrowdSecReloadError", "restart_container"]
