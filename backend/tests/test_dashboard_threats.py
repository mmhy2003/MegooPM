"""Grouping CrowdSec alerts into map points.

Pure: no network. The map's whole contract is this list, and it is deliberately
not CrowdSec-shaped so a future request-analytics pipeline can produce the same
type without the map component changing.
"""

from __future__ import annotations

from app.schemas.crowdsec import Alert, AlertSource
from app.services.dashboard.threats import group_by_country


def _alert(cn: str | None, *, lat: float | None = None, lng: float | None = None) -> Alert:
    return Alert(
        scenario="x",
        source=AlertSource(ip="1.2.3.4", cn=cn, latitude=lat, longitude=lng),
    )


def test_groups_alerts_by_country() -> None:
    points = group_by_country(
        [_alert("DE"), _alert("DE"), _alert("FR")]
    )
    assert {p.country: p.count for p in points} == {"DE": 2, "FR": 1}


def test_points_are_ordered_by_count_descending() -> None:
    """A stable order, so identical polls do not reshuffle the map's legend."""
    points = group_by_country([_alert("FR"), _alert("DE"), _alert("DE")])
    assert [p.country for p in points] == ["DE", "FR"]


def test_ties_break_alphabetically_so_the_order_is_deterministic() -> None:
    points = group_by_country([_alert("FR"), _alert("DE")])
    assert [p.country for p in points] == ["DE", "FR"]


def test_alerts_with_no_country_are_dropped() -> None:
    """A bucket labelled 'unknown' cannot be placed on a map, and it would
    distort the ranking of the countries that can."""
    points = group_by_country([_alert(None), _alert("DE")])
    assert [p.country for p in points] == ["DE"]


def test_alerts_with_no_source_are_dropped() -> None:
    points = group_by_country([Alert(scenario="x", source=None), _alert("DE")])
    assert [p.country for p in points] == ["DE"]


def test_coordinates_come_from_the_alerts_themselves() -> None:
    """CrowdSec geolocates every alert, so the point is the real mean position
    of the attackers rather than a static country centroid we would have to
    ship and maintain."""
    points = group_by_country(
        [_alert("DE", lat=52.0, lng=13.0), _alert("DE", lat=48.0, lng=11.0)]
    )
    assert points[0].lat == 50.0
    assert points[0].lng == 12.0


def test_a_country_whose_alerts_carry_no_coordinates_is_still_counted() -> None:
    """It cannot be plotted, but dropping it would hide a real attacker from
    the ranked list — the map is best-effort, the count is not."""
    points = group_by_country([_alert("DE"), _alert("DE")])
    assert points[0].count == 2
    assert points[0].lat is None
    assert points[0].lng is None


def test_a_country_with_mixed_coordinates_averages_only_the_known_ones() -> None:
    points = group_by_country([_alert("DE", lat=52.0, lng=13.0), _alert("DE")])
    assert points[0].count == 2
    assert points[0].lat == 52.0


def test_country_codes_are_normalised_to_upper_case() -> None:
    points = group_by_country([_alert("de"), _alert("DE")])
    assert [p.country for p in points] == ["DE"]
    assert points[0].count == 2


def test_no_alerts_is_an_empty_list_not_an_error() -> None:
    assert group_by_country([]) == []
