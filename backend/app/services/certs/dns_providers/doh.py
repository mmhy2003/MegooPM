"""DNS-over-HTTPS lookups for the DNS-01 propagation check.

Some networks let only HTTPS out. Port 53 is firewalled, and the local resolver
is an internal server that may host its own copy of the zone (split-horizon) —
an Active Directory domain named like the public one is the common case. Asking
that resolver who a zone's nameservers are returns a confident, wrong answer,
and querying the real ones on port 53 times out.

The JSON flavour of DoH (``application/dns-json``) is served by Cloudflare and
Google alike, and needs nothing but an HTTP client this project already has.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

import httpx

DEFAULT_DOH_URLS = (
    "https://cloudflare-dns.com/dns-query",
    "https://dns.google/resolve",
)

#: Record type numbers as they appear in a DoH JSON answer.
_TYPES = {"A": 1, "NS": 2, "TXT": 16}
#: DNS RCODEs a DoH JSON answer reports in ``Status``.
_STATUS = {2: "SERVFAIL", 3: "NXDOMAIN", 5: "REFUSED"}
_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')


class DohError(RuntimeError):
    """A DoH lookup failed, or the name does not exist."""


def _answers(
    url: str,
    name: str,
    rtype: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = 5.0,
) -> list[str]:
    """The ``data`` of every answer of ``rtype`` for ``name``, or raise DohError."""
    params = {"name": name.rstrip("."), "type": rtype}
    headers = {"accept": "application/dns-json"}
    try:
        if client is not None:
            response = client.get(url, params=params, headers=headers, timeout=timeout)
        else:
            response = httpx.get(url, params=params, headers=headers, timeout=timeout)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise DohError(f"{url}: {type(exc).__name__}: {exc}") from exc

    status = body.get("Status")
    if status != 0:
        raise DohError(f"{url}: {_STATUS.get(status, f'status {status}')} for {name} {rtype}")
    wanted = _TYPES[rtype]
    return [answer["data"] for answer in body.get("Answer", []) if answer.get("type") == wanted]


def _txt_value(data: str) -> str:
    """A TXT answer's value: quoted strings joined, as DNS joins them.

    Cloudflare returns the presentation form (``"abc" "def"``); Google returns
    the value bare. Both are handled, because both are in the default list.
    """
    chunks = _QUOTED.findall(data)
    if not chunks:
        return data
    return "".join(chunk.replace('\\"', '"').replace("\\\\", "\\") for chunk in chunks)


def query_txt_doh(url: str, name: str, *, client: httpx.Client | None = None) -> set[str]:
    """TXT values the resolver at ``url`` currently returns for ``name``."""
    return {_txt_value(data) for data in _answers(url, name, "TXT", client=client)}


def nameservers_doh(
    zone: str,
    urls: Iterable[str],
    *,
    client: httpx.Client | None = None,
) -> list[str]:
    """IPv4 addresses of ``zone``'s public nameservers, in NS order.

    The first resolver that answers wins; the next is tried only if one cannot
    be reached. IPv4 only, like the authoritative check on Docker's default
    bridge network, which has no IPv6 route.
    """
    failures: list[str] = []
    for url in urls:
        try:
            hosts = _answers(url, zone, "NS", client=client)
            addresses: list[str] = []
            for host in hosts:
                try:
                    addresses.extend(_answers(url, host, "A", client=client))
                except DohError:
                    continue  # one nameserver without an A record is not fatal
            if addresses:
                return addresses
            failures.append(f"{url}: no nameserver addresses for {zone}")
        except DohError as exc:
            failures.append(str(exc))
    raise DohError("; ".join(failures) or f"no DoH resolvers configured for {zone}")


__all__ = ["DEFAULT_DOH_URLS", "DohError", "nameservers_doh", "query_txt_doh"]
