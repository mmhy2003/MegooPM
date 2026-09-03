"""Which address a request really came from.

uvicorn runs without ``--proxy-headers``, and port 8000 is both proxied by
nginx and published directly. So ``request.client.host`` is the proxy for one
path and the real client for the other.

The rule: trust ``X-Forwarded-For`` only when the connection itself came from a
private range (RFC 1918, loopback) — that is nginx in the compose network, or
an operator on the LAN. A public client's header is ignored, so it cannot spoof
its way into another bucket. A LAN attacker forging the header is outside a
rate limit's threat model.

Reads the *rightmost* forwarded address: nginx appends the address that
connected to it, so anything a client prepended sits to the left.
"""

from __future__ import annotations

import ipaddress

from starlette.requests import Request

_TRUSTED = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
)


def _is_trusted(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in _TRUSTED)


def _valid(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def client_ip(request: Request) -> str:
    """The address to rate-limit on. Never raises."""
    connecting = request.client.host if request.client else None
    if not connecting:
        return "unknown"
    if not _is_trusted(connecting):
        return connecting
    forwarded = request.headers.get("x-forwarded-for", "")
    candidates = [part.strip() for part in forwarded.split(",") if part.strip()]
    if candidates and _valid(candidates[-1]):
        return candidates[-1]
    return connecting


__all__ = ["client_ip"]
