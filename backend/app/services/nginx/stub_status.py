"""Parser for nginx's ``stub_status`` body.

Pure: no network. The scrape lives in the task; this only turns text into
numbers, which is what makes the counter arithmetic testable without nginx.

The format is fixed, and nginx emits a trailing space on most lines::

    Active connections: 43
    server accepts handled requests
     1204 1204 9001
    Reading: 0 Writing: 5 Waiting: 38
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ACTIVE = re.compile(r"Active connections:\s+(\d+)")
# Anchored to a line holding nothing but three integers. The `Reading: 0
# Writing: 1 Waiting: 0` line also carries three numbers, and an unanchored
# pattern would match it and report connection states as request counters.
_COUNTERS = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s*$", re.MULTILINE)


class ParseError(ValueError):
    """The body was not a stub_status page.

    Raised rather than returning zeros: an error page parsed as "0 connections"
    is indistinguishable from a genuinely idle server, so the dashboard would
    report a confident lie instead of an outage.
    """


@dataclass(frozen=True, slots=True)
class StubStatus:
    """One sample.

    ``accepted`` / ``handled`` / ``requests`` are cumulative since the worker
    started, so a rate is a delta between two samples — never a single reading.
    """

    active: int
    accepted: int
    handled: int
    requests: int


def parse_stub_status(text: str) -> StubStatus:
    """Turn a stub_status body into numbers, or raise :class:`ParseError`."""
    active = _ACTIVE.search(text)
    counters = _COUNTERS.search(text)
    if active is None or counters is None:
        raise ParseError("not a stub_status body")
    return StubStatus(
        active=int(active.group(1)),
        accepted=int(counters.group(1)),
        handled=int(counters.group(2)),
        requests=int(counters.group(3)),
    )


__all__ = ["ParseError", "StubStatus", "parse_stub_status"]
