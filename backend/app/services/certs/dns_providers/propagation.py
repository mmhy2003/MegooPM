"""Wait for a DNS-01 TXT record to be visible on the zone's authoritative servers.

The ACME server resolves ``_acme-challenge.<domain>`` itself, so answering the
challenge before every authoritative nameserver serves the record is the
classic way to fail validation (and burn rate limits). We query the
authoritative servers directly — recursive resolvers may cache a negative
answer for minutes.
"""

from __future__ import annotations

import socket
import time
from collections import Counter
from collections.abc import Callable

import dns.resolver

from app.services.certs.dns_providers.lexicon_provider import zone_for


class PropagationTimeoutError(RuntimeError):
    """The TXT record did not appear on every authoritative nameserver in time."""


# A global address to ask the kernel for a route to. Nothing is ever sent there.
_IPV6_PROBE_ADDRESS = ("2001:4860:4860::8888", 53)


def _probe_ipv6_route() -> None:
    with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sock:
        sock.connect(_IPV6_PROBE_ADDRESS)


def ipv6_available(*, probe: Callable[[], None] = _probe_ipv6_route) -> bool:
    """True when this host has a route for global IPv6 traffic.

    ``connect`` on a UDP socket transmits nothing — it only asks the kernel for
    a route — so this is a local, instant check. Docker's default bridge network
    has no IPv6, where the probe raises ``OSError: Network is unreachable``.
    """
    try:
        probe()
    except OSError:
        return False
    return True


def authoritative_nameservers(
    zone: str,
    *,
    resolver: dns.resolver.Resolver | None = None,
    ipv6: bool | None = None,
) -> list[str]:
    """IP addresses of ``zone``'s NS hosts, IPv4 first, in NS order.

    The AAAA addresses are dropped unless this host can actually route IPv6
    (``ipv6=None`` probes for it). They are the very same nameservers already
    reachable over IPv4, so nothing is lost — while on an IPv4-only worker every
    query sent to them times out and reads as "the record has not propagated".
    """
    resolver = resolver or dns.resolver.Resolver()
    v4: list[str] = []
    v6: list[str] = []
    for ns in resolver.resolve(zone, "NS"):
        host = ns.target.to_text()
        for rtype, addresses in (("A", v4), ("AAAA", v6)):
            try:
                addresses.extend(rdata.to_text() for rdata in resolver.resolve(host, rtype))
            except Exception:  # noqa: BLE001 - a missing AAAA/A is normal
                continue
    if ipv6 is None:
        ipv6 = ipv6_available()
    if ipv6:
        return v4 + v6
    if v6 and not v4:
        raise PropagationTimeoutError(
            f"{zone} is served only by IPv6 nameservers and this host has no IPv6 route"
        )
    return v4


def query_txt(nameserver: str, name: str, *, lifetime: float = 5.0) -> set[str]:
    """TXT values ``nameserver`` currently serves for ``name`` (raises on lookup errors)."""
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = [nameserver]
    resolver.lifetime = lifetime
    answer = resolver.resolve(name.rstrip("."), "TXT")
    return {b"".join(rdata.strings).decode("utf-8", "replace") for rdata in answer}


def wait_for_txt(
    name: str,
    value: str,
    *,
    timeout_seconds: int,
    interval_seconds: int,
    settle_seconds: int = 0,
    nameservers: list[str] | None = None,
    query: Callable[[str, str], set[str]] = query_txt,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Block until every authoritative nameserver serves ``value`` at ``name``.

    Raises :class:`PropagationTimeoutError` after ``timeout_seconds``. A query
    error (SERVFAIL, timeout, NXDOMAIN) simply counts as "not yet", but the
    timeout message keeps the two apart: a nameserver we cannot reach at all is
    a very different problem from one still serving the previous record.

    ``settle_seconds`` is an extra grace period once every nameserver answers.
    The NS addresses of anycast providers (Cloudflare, Route 53, ...) all route
    to the *nearest* PoP, so a positive answer here proves one vantage point,
    while the ACME server validates from several. Answering immediately let
    Let's Encrypt's remote validators see the previous record set ("During
    secondary validation: Incorrect TXT record ... (and 1 more)").
    """
    servers = nameservers if nameservers is not None else authoritative_nameservers(zone_for(name))
    if not servers:
        raise PropagationTimeoutError(f"No authoritative nameservers found for {name!r}")

    deadline = clock() + timeout_seconds
    pending = list(servers)
    while True:
        # Only servers that have not served the value yet are polled again; a
        # server that answered correctly once is not re-checked.
        still_pending = []
        reasons: dict[str, str] = {}
        for server in pending:
            try:
                served = query(server, name)
            except Exception as exc:  # noqa: BLE001 - any lookup failure means "not propagated"
                still_pending.append(server)
                reasons[server] = f"unreachable ({type(exc).__name__})"
                continue
            if value not in served:
                still_pending.append(server)
                reasons[server] = "serving other values"
        pending = still_pending
        if not pending:
            if settle_seconds > 0:
                sleep(settle_seconds)
            return
        if clock() >= deadline:
            detail = ", ".join(
                f"{count} {reason}" for reason, count in Counter(reasons.values()).most_common()
            )
            raise PropagationTimeoutError(
                f"TXT {name} not visible on {len(pending)}/{len(servers)} authoritative "
                f"nameservers after {timeout_seconds}s ({detail})"
            )
        sleep(interval_seconds)


__all__ = [
    "PropagationTimeoutError",
    "authoritative_nameservers",
    "ipv6_available",
    "query_txt",
    "wait_for_txt",
]
