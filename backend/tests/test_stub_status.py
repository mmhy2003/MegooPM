"""Tests for the nginx stub_status parser.

Pure: no nginx, no network. The format is fixed and ancient, but a misparse
reports wrong numbers on the dashboard rather than failing, so it is worth
pinning exactly.

``BODY`` is the real response captured from the ``megoopm-nginx`` image, built
by concatenation so the **trailing space on every line** — which nginx really
emits — stays visible and survives any formatter.
"""

from __future__ import annotations

import pytest
from app.services.nginx.stub_status import ParseError, parse_stub_status

BODY = (
    "Active connections: 43 \n"
    "server accepts handled requests\n"
    " 1204 1204 9001 \n"
    "Reading: 0 Writing: 5 Waiting: 38 \n"
)


def test_parses_the_real_nginx_body() -> None:
    got = parse_stub_status(BODY)
    assert got.active == 43
    assert got.accepted == 1204
    assert got.handled == 1204
    assert got.requests == 9001


def test_rejects_a_body_that_is_not_stub_status() -> None:
    """An error page is HTML; parsing it as numbers would report noise as a
    connection count instead of surfacing the outage."""
    with pytest.raises(ParseError):
        parse_stub_status("<html><body>404 Not Found</body></html>")


def test_rejects_a_truncated_body() -> None:
    with pytest.raises(ParseError):
        parse_stub_status("Active connections: 43 \n")


def test_rejects_an_empty_body() -> None:
    with pytest.raises(ParseError):
        parse_stub_status("")


def test_tolerates_extra_whitespace() -> None:
    got = parse_stub_status(
        "Active connections:   7 \nserver accepts handled requests\n   1 2 3 \n"
    )
    assert (got.active, got.accepted, got.handled, got.requests) == (7, 1, 2, 3)


def test_does_not_mistake_the_reading_line_for_the_counters() -> None:
    """`Reading: 0 Writing: 1 Waiting: 0` also holds three numbers. Matching it
    would silently report connection states as request counters."""
    got = parse_stub_status(BODY)
    assert got.requests == 9001
