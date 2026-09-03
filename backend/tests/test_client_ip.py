from __future__ import annotations

from app.core.client_ip import client_ip
from starlette.requests import Request


def _request(*, client_host: str, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (client_host, 12345),
    }
    return Request(scope)


def test_a_direct_public_client_is_its_own_address() -> None:
    assert client_ip(_request(client_host="203.0.113.7")) == "203.0.113.7"


def test_a_public_client_cannot_spoof_the_header() -> None:
    # The header is ignored unless the connection came from a trusted range.
    req = _request(client_host="203.0.113.7", forwarded="10.0.0.1")
    assert client_ip(req) == "203.0.113.7"


def test_behind_the_proxy_the_forwarded_address_wins() -> None:
    # nginx in the compose network connects from a private address and appends
    # the real client to X-Forwarded-For.
    req = _request(client_host="172.18.0.5", forwarded="203.0.113.7")
    assert client_ip(req) == "203.0.113.7"


def test_the_rightmost_forwarded_address_is_used() -> None:
    # A client that sent its own X-Forwarded-For has it *prepended* to; the
    # address nginx appended — the one that actually connected to it — is last.
    req = _request(client_host="172.18.0.5", forwarded="1.2.3.4, 203.0.113.7")
    assert client_ip(req) == "203.0.113.7"


def test_loopback_is_trusted_too() -> None:
    req = _request(client_host="127.0.0.1", forwarded="203.0.113.7")
    assert client_ip(req) == "203.0.113.7"


def test_a_private_client_with_no_header_is_itself() -> None:
    # An operator on the LAN hitting port 8000 directly.
    assert client_ip(_request(client_host="192.168.1.20")) == "192.168.1.20"


def test_a_garbage_header_falls_back_to_the_connection() -> None:
    req = _request(client_host="172.18.0.5", forwarded="not an address")
    assert client_ip(req) == "172.18.0.5"


def test_no_client_at_all_is_unknown() -> None:
    # ASGI test transports sometimes omit `client`. Never raise here — this
    # runs on an unauthenticated route.
    req = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    assert client_ip(req) == "unknown"
