"""The country lookup.

Pure apart from reading a file. The rule under test is that every failure path
returns ``None`` rather than raising: this runs inside a flush batch, so one bad
address must not cost a whole minute of visitor data.

These tests pass whether or not the database shipped, deliberately — the suite
must not depend on a build-time download.
"""

from __future__ import annotations

from app.services.analytics import geoip
from app.services.analytics.geoip import lookup_country


def test_a_private_address_has_no_country() -> None:
    assert lookup_country("10.0.0.1") is None


def test_a_malformed_address_returns_none_rather_than_raising() -> None:
    """The value arrives from a Redis hash field, which is attacker-influenced:
    anything that can reach the proxy becomes a key."""
    assert lookup_country("not-an-ip") is None
    assert lookup_country("") is None
    assert lookup_country("1.2.3.4.5") is None


def test_a_missing_database_disables_lookups_without_raising(monkeypatch) -> None:
    monkeypatch.setattr(geoip.settings, "geoip_database_path", "/nonexistent.mmdb")
    geoip.reset_reader()
    try:
        assert lookup_country("8.8.8.8") is None
        assert geoip.database_available() is False
    finally:
        geoip.reset_reader()


def test_a_corrupt_database_disables_lookups_without_raising(
    monkeypatch, tmp_path
) -> None:
    """A truncated or half-downloaded file must degrade, not crash the worker."""
    bad = tmp_path / "corrupt.mmdb"
    bad.write_bytes(b"not an mmdb")
    monkeypatch.setattr(geoip.settings, "geoip_database_path", str(bad))
    geoip.reset_reader()
    try:
        assert lookup_country("8.8.8.8") is None
        assert geoip.database_available() is False
    finally:
        geoip.reset_reader()
