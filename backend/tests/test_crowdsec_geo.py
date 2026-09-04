"""Country codes for the Security page's IP columns."""

from __future__ import annotations

import pytest
from app.schemas.crowdsec import Alert, AlertSource, Decision
from app.services.crowdsec import geo

LOOKUPS = {"203.0.113.9": "DE", "198.51.100.0": "US"}


@pytest.fixture(autouse=True)
def fake_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(geo, "lookup_country", lambda ip: LOOKUPS.get(ip))


def test_ip_scope_looks_the_address_up() -> None:
    assert geo.country_for("Ip", "203.0.113.9") == "DE"
    assert geo.country_for("ip", " 203.0.113.9 ") == "DE"
    assert geo.country_for("Ip", "10.0.0.1") is None


def test_range_scope_looks_the_network_address_up() -> None:
    # A country-level database answers the same for the whole block.
    assert geo.country_for("Range", "198.51.100.77/24") == "US"
    assert geo.country_for("Range", "not a range") is None


def test_country_scope_is_the_code_itself() -> None:
    assert geo.country_for("Country", "fr") == "FR"
    assert geo.country_for("Country", "France") is None


def test_other_scopes_have_no_country() -> None:
    assert geo.country_for("AS", "AS64496") is None
    assert geo.country_for(None, "x") is None
    assert geo.country_for("Ip", None) is None


def test_enrich_decisions_fills_only_the_blanks() -> None:
    known = Decision(type="ban", scope="Ip", value="203.0.113.9", duration="1h", country="ZZ")
    blank = Decision(type="ban", scope="Ip", value="203.0.113.9", duration="1h")
    other = Decision(type="ban", scope="AS", value="AS1", duration="1h")
    out = geo.enrich_decisions([known, blank, other])
    assert [d.country for d in out] == ["ZZ", "DE", None]
    # Inputs are not mutated.
    assert blank.country is None


def test_enrich_alerts_respects_crowdsecs_own_answer() -> None:
    enriched = Alert(source=AlertSource(ip="203.0.113.9", cn="XX"))
    bare = Alert(source=AlertSource(ip="203.0.113.9"))
    ranged = Alert(source=AlertSource(scope="Range", value="198.51.100.0/24"))
    nothing = Alert(source=None)
    out = geo.enrich_alerts([enriched, bare, ranged, nothing])
    assert out[0].source is not None and out[0].source.cn == "XX"
    assert out[1].source is not None and out[1].source.cn == "DE"
    assert out[2].source is not None and out[2].source.cn == "US"
    assert out[3].source is None
