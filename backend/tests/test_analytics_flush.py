"""Turning Redis counters into visitor rows.

``parse_counters`` is pure and carries the risk: it reads untrusted hash fields
— anything that can reach the proxy becomes a key — and produces the rows a
database write is built from.
"""

from __future__ import annotations

from datetime import date

from app.services.analytics.flush import parse_counters

DAY = date(2026, 9, 2)


def test_pairs_counts_with_bytes() -> None:
    rows = parse_counters({"1.2.3.4": "10"}, {"1.2.3.4": "2048"}, DAY)
    assert len(rows) == 1
    assert rows[0].ip == "1.2.3.4"
    assert rows[0].requests == 10
    assert rows[0].bytes == 2048
    assert rows[0].day == DAY


def test_an_ip_with_no_byte_counter_still_produces_a_row() -> None:
    """The two hashes are separate commands, so a crash between them can leave
    one behind. Losing the visitor entirely would be worse than losing bytes."""
    rows = parse_counters({"1.2.3.4": "10"}, {}, DAY)
    assert rows[0].requests == 10
    assert rows[0].bytes == 0


def test_a_byte_counter_with_no_request_counter_is_ignored() -> None:
    """Driven by the count hash: bytes alone describe no requests to add."""
    assert parse_counters({}, {"1.2.3.4": "50"}, DAY) == []


def test_a_non_numeric_counter_is_skipped_not_fatal() -> None:
    """One bad value must not cost the whole batch."""
    rows = parse_counters({"1.2.3.4": "abc", "5.6.7.8": "3"}, {}, DAY)
    assert [r.ip for r in rows] == ["5.6.7.8"]


def test_a_non_numeric_byte_value_falls_back_to_zero() -> None:
    rows = parse_counters({"1.2.3.4": "3"}, {"1.2.3.4": "junk"}, DAY)
    assert rows[0].requests == 3
    assert rows[0].bytes == 0


def test_values_are_accepted_as_strings_or_bytes() -> None:
    """redis-py returns bytes unless decode_responses is set; accepting both
    means a client-config change cannot silently zero every counter."""
    rows = parse_counters({b"1.2.3.4": b"7"}, {b"1.2.3.4": b"14"}, DAY)
    assert rows[0].ip == "1.2.3.4"
    assert rows[0].requests == 7
    assert rows[0].bytes == 14


def test_an_empty_ip_field_is_skipped() -> None:
    assert parse_counters({"": "5"}, {}, DAY) == []


def test_no_counters_is_an_empty_list() -> None:
    assert parse_counters({}, {}, DAY) == []
