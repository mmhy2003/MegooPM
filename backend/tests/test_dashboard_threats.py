"""Grouping CrowdSec alerts into map points.

Pure: no network. The service counts; the map places. Both the threat and the
visitor layers reach the map as {country, count}, which is what lets one
centroid table position them consistently.
"""

from __future__ import annotations

from app.schemas.crowdsec import Alert, AlertSource
from app.services.dashboard.threats import group_by_country


def _alert(cn: str | None) -> Alert:
    return Alert(scenario="x", source=AlertSource(ip="1.2.3.4", cn=cn))


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


def test_a_country_is_counted_regardless_of_where_it_is() -> None:
    """Placement moved to the map, so the service only counts. A country the
    map cannot place is still a real country with real attacks."""
    points = group_by_country([_alert("DE"), _alert("DE")])
    assert points[0].country == "DE"
    assert points[0].count == 2
    assert not hasattr(points[0], "lat")


def test_country_codes_are_normalised_to_upper_case() -> None:
    points = group_by_country([_alert("de"), _alert("DE")])
    assert [p.country for p in points] == ["DE"]
    assert points[0].count == 2


def test_no_alerts_is_an_empty_list_not_an_error() -> None:
    assert group_by_country([]) == []
