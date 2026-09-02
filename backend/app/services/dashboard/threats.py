"""Turn CrowdSec alerts into map points.

Pure: no network, no database. The service counts; the **map** places. Both
this and the visitor countries reach the map as ``{country, count}``, so one
centroid table positions them and a country never appears at two slightly
different points depending on which layer drew it.

CrowdSec does resolve real coordinates per alert, and this once averaged them
per country. That average said roughly *where in* a country the attackers were
— genuinely more than a centroid — but the map groups by country and cannot
express the difference, so it was not worth two placement rules.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.schemas.crowdsec import Alert
from app.schemas.dashboard import ThreatPoint


def group_by_country(alerts: Iterable[Alert]) -> list[ThreatPoint]:
    """Count alerts per country.

    An alert with no country is dropped: an "unknown" bucket cannot be drawn on
    a map and would distort the ranking of the countries that can.
    """
    counts: dict[str, int] = {}
    for alert in alerts:
        source = alert.source
        if source is None or not source.cn:
            continue
        code = source.cn.upper()
        counts[code] = counts.get(code, 0) + 1

    # Count descending, then country ascending: a stable order, so two
    # identical polls do not reshuffle the map's legend under the operator.
    ordered = sorted(counts, key=lambda code: (-counts[code], code))
    return [ThreatPoint(country=code, count=counts[code]) for code in ordered]


__all__ = ["group_by_country"]
