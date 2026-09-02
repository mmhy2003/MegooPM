"""Turn CrowdSec alerts into points a map can draw.

Pure: no network, no database. The output type is deliberately not
CrowdSec-shaped, so a future request-analytics pipeline can produce the same
list and the map component never learns where its data came from.

**No centroid table.** CrowdSec's ``geoip-enrich`` parser already resolves
coordinates for every alert ("Populate event with geoloc info : as, country,
coords, source range"), so a country's point is the mean position of the
attackers actually seen. Shipping a static table of ~250 country centroids
would be more data to maintain, less accurate, and wrong in a way nobody would
notice.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.schemas.crowdsec import Alert
from app.schemas.dashboard import ThreatPoint


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def group_by_country(alerts: Iterable[Alert]) -> list[ThreatPoint]:
    """Count alerts per country, positioned by their own coordinates.

    An alert with no country is dropped: an "unknown" bucket cannot be placed
    on a map and would distort the ranking of the countries that can. A country
    whose alerts carry no coordinates is *kept* but unplottable — the count is
    real even when the position is not, and hiding it would omit an attacker
    from the list to keep the map tidy.
    """
    counts: dict[str, int] = {}
    lats: dict[str, list[float]] = {}
    lngs: dict[str, list[float]] = {}

    for alert in alerts:
        source = alert.source
        if source is None or not source.cn:
            continue
        code = source.cn.upper()
        counts[code] = counts.get(code, 0) + 1
        if source.latitude is not None:
            lats.setdefault(code, []).append(source.latitude)
        if source.longitude is not None:
            lngs.setdefault(code, []).append(source.longitude)

    # Count descending, then country ascending: a stable order, so two
    # identical polls do not reshuffle the map's legend under the operator.
    ordered = sorted(counts, key=lambda code: (-counts[code], code))
    return [
        ThreatPoint(
            country=code,
            count=counts[code],
            lat=_mean(lats.get(code, [])),
            lng=_mean(lngs.get(code, [])),
        )
        for code in ordered
    ]


__all__ = ["group_by_country"]
