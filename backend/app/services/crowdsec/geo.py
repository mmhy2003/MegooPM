"""Country codes for the Security page's IP columns.

CrowdSec's alerts already carry ``source.cn`` when its geoip-enrich parser is
installed; decisions carry nothing. Both are filled from the bundled DB-IP
country database the analytics map uses, so the UI can show a flag without a
second request or a dependency on which parsers happen to be installed.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence

from app.schemas.crowdsec import Alert, Decision
from app.services.analytics.geoip import lookup_country


def country_for(scope: str | None, value: str | None) -> str | None:
    """ISO-3166 alpha-2 for a decision's target, or ``None``.

    ``Ip`` looks the address up; ``Range`` looks its network address up (a
    country-level database answers the same for the whole block); ``Country``
    *is* a code already. Anything else — ``AS``, custom scopes — has no
    country.
    """
    if not scope or not value:
        return None
    kind = scope.lower()
    if kind == "country":
        code = value.strip().upper()
        return code if len(code) == 2 and code.isalpha() else None
    if kind == "ip":
        return _normalise(lookup_country(value.strip()))
    if kind == "range":
        try:
            network = ipaddress.ip_network(value.strip(), strict=False)
        except ValueError:
            return None
        return _normalise(lookup_country(str(network.network_address)))
    return None


def _normalise(code: str | None) -> str | None:
    if not code:
        return None
    code = code.strip().upper()
    return code if len(code) == 2 and code.isalpha() else None


def enrich_decisions(decisions: Sequence[Decision]) -> list[Decision]:
    """Copies of ``decisions`` with ``country`` filled where it can be."""
    return [
        d.model_copy(update={"country": country_for(d.scope, d.value)}) if d.country is None else d
        for d in decisions
    ]


def enrich_alerts(alerts: Sequence[Alert]) -> list[Alert]:
    """Fill ``source.cn`` from the address when CrowdSec did not."""
    out: list[Alert] = []
    for alert in alerts:
        source = alert.source
        if source is None or source.cn:
            out.append(alert)
            continue
        code = country_for(source.scope or "Ip", source.ip or source.value)
        if code is None:
            out.append(alert)
            continue
        out.append(alert.model_copy(update={"source": source.model_copy(update={"cn": code})}))
    return out


__all__ = ["country_for", "enrich_alerts", "enrich_decisions"]
