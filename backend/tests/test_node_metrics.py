"""Recording and aggregating per-node nginx samples.

The rate arithmetic and the staleness rule are pure and carry the risk: a
mistake here reports a wrong number confidently rather than failing, which on a
dashboard is worse than showing nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.dashboard.metrics import aggregate, compute_rate
from app.services.nginx.stub_status import StubStatus

NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


class _Row:
    """A stand-in for NodeMetrics; aggregate() only reads attributes."""

    def __init__(self, node_id: str, active: int, rps: float, sampled_at: datetime) -> None:
        self.node_id = node_id
        self.active_connections = active
        self.requests_per_second = rps
        self.sampled_at = sampled_at


# --- Aggregation -----------------------------------------------------------


def test_totals_sum_across_reporting_nodes() -> None:
    rows = [
        _Row("a", 10, 2.0, NOW - timedelta(seconds=5)),
        _Row("b", 7, 1.5, NOW - timedelta(seconds=5)),
    ]
    got = aggregate(rows, now=NOW, stale_after=60)
    assert got.active_connections == 17
    assert got.requests_per_second == 3.5
    assert got.reporting_nodes == 2
    assert got.stale_nodes == 0


def test_a_stale_node_is_excluded_not_counted_as_zero() -> None:
    """It has unknown connections, not none — counting it as zero would make a
    dead node look like an idle one."""
    rows = [
        _Row("a", 10, 2.0, NOW - timedelta(seconds=5)),
        _Row("b", 999, 99.0, NOW - timedelta(seconds=600)),
    ]
    got = aggregate(rows, now=NOW, stale_after=60)
    assert got.active_connections == 10
    assert got.reporting_nodes == 1
    assert got.stale_nodes == 1


def test_a_node_exactly_at_the_cutoff_is_still_live() -> None:
    rows = [_Row("a", 4, 1.0, NOW - timedelta(seconds=60))]
    got = aggregate(rows, now=NOW, stale_after=60)
    assert got.reporting_nodes == 1


def test_no_rows_reports_nothing_rather_than_zero() -> None:
    """Before any scrape has run there is no measurement; the card must be able
    to say so instead of claiming the server is idle."""
    got = aggregate([], now=NOW, stale_after=60)
    assert got.reporting_nodes == 0
    assert got.active_connections is None
    assert got.requests_per_second is None


def test_every_node_stale_reports_nothing_and_counts_them() -> None:
    rows = [_Row("a", 5, 1.0, NOW - timedelta(seconds=600))]
    got = aggregate(rows, now=NOW, stale_after=60)
    assert got.active_connections is None
    assert got.stale_nodes == 1


# --- Rate arithmetic -------------------------------------------------------


def test_rate_is_the_delta_over_elapsed_time() -> None:
    previous = (1000, NOW - timedelta(seconds=10))
    assert compute_rate(StubStatus(1, 0, 0, 1100), previous, now=NOW) == 10.0


def test_a_counter_reset_reports_zero_not_a_negative_rate() -> None:
    """nginx restarting zeroes the counters. Subtracting gives a negative rate,
    which would render as a nonsensical figure."""
    previous = (9000, NOW - timedelta(seconds=10))
    assert compute_rate(StubStatus(1, 0, 0, 5), previous, now=NOW) == 0.0


def test_the_first_sample_has_no_rate_yet() -> None:
    assert compute_rate(StubStatus(1, 0, 0, 500), None, now=NOW) == 0.0


def test_two_samples_at_the_same_instant_do_not_divide_by_zero() -> None:
    assert compute_rate(StubStatus(1, 0, 0, 600), (500, NOW), now=NOW) == 0.0


def test_a_clock_that_went_backwards_reports_zero() -> None:
    """Negative elapsed time would flip the sign of a legitimate delta."""
    previous = (500, NOW + timedelta(seconds=10))
    assert compute_rate(StubStatus(1, 0, 0, 600), previous, now=NOW) == 0.0
