"""Wait for a DNS-01 TXT record to be visible on the zone's authoritative servers.

The ACME server resolves ``_acme-challenge.<domain>`` itself, so answering the
challenge before every authoritative nameserver serves the record is the
classic way to fail validation (and burn rate limits). We query the
authoritative servers directly — recursive resolvers may cache a negative
answer for minutes.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import dns.resolver

from app.services.certs.dns_providers.lexicon_provider import zone_for


class PropagationTimeoutError(RuntimeError):
    """The TXT record did not appear on every authoritative nameserver in time."""


def authoritative_nameservers(
    zone: str, *, resolver: dns.resolver.Resolver | None = None
) -> list[str]:
    """IP addresses of ``zone``'s NS hosts (A and AAAA), in NS order."""
    resolver = resolver or dns.resolver.Resolver()
    addresses: list[str] = []
    for ns in resolver.resolve(zone, "NS"):
        host = ns.target.to_text()
        for rtype in ("A", "AAAA"):
            try:
                addresses.extend(rdata.to_text() for rdata in resolver.resolve(host, rtype))
            except Exception:  # noqa: BLE001 - a missing AAAA/A is normal
                continue
    return addresses


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
    nameservers: list[str] | None = None,
    query: Callable[[str, str], set[str]] = query_txt,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Block until every authoritative nameserver serves ``value`` at ``name``.

    Raises :class:`PropagationTimeoutError` after ``timeout_seconds``. A query
    error (SERVFAIL, timeout, NXDOMAIN) simply counts as "not yet".
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
        for server in pending:
            try:
                served = query(server, name)
            except Exception:  # noqa: BLE001 - treat any lookup failure as not propagated
                served = set()
            if value not in served:
                still_pending.append(server)
        pending = still_pending
        if not pending:
            return
        if clock() >= deadline:
            raise PropagationTimeoutError(
                f"TXT {name} not visible on {len(pending)}/{len(servers)} authoritative "
                f"nameservers after {timeout_seconds}s"
            )
        sleep(interval_seconds)


__all__ = ["PropagationTimeoutError", "authoritative_nameservers", "query_txt", "wait_for_txt"]
