"""Validation rules for proxy host location paths (pure pydantic, no DB)."""

from __future__ import annotations

import pytest
from app.schemas.proxy_host import ProxyHostCreate, ProxyHostLocationIn, ProxyHostUpdate
from pydantic import ValidationError


def _create(**locations_kw) -> ProxyHostCreate:
    return ProxyHostCreate(domain_names=["a.example.com"], upstream_id=1, **locations_kw)


def test_location_defaults_to_http_and_keeps_trailing_slash_distinct() -> None:
    loc = ProxyHostLocationIn(path="/api/", upstream_id=2)
    assert loc.forward_scheme == "http"
    assert loc.path == "/api/"
    assert ProxyHostLocationIn(path="/api", upstream_id=2).path == "/api"


@pytest.mark.parametrize(
    ("path", "fragment"),
    [
        ("api", "start with '/'"),
        ("/", "root"),
        ("/a b", "whitespace"),
        ("/a;b", "whitespace"),
        ('/a"b', "whitespace"),
        ("/a{b}", "whitespace"),
        ("/" + "x" * 255, "255"),
    ],
)
def test_invalid_paths_are_rejected(path: str, fragment: str) -> None:
    with pytest.raises(ValidationError, match=fragment):
        ProxyHostLocationIn(path=path, upstream_id=2)


def test_duplicate_paths_in_one_payload_are_rejected() -> None:
    rows = [{"path": "/api", "upstream_id": 2}, {"path": "/api", "upstream_id": 3}]
    with pytest.raises(ValidationError, match="duplicate location path"):
        _create(locations=rows)
    with pytest.raises(ValidationError, match="duplicate location path"):
        ProxyHostUpdate(locations=rows)


def test_locations_default_empty_and_update_none_means_unchanged() -> None:
    assert _create().locations == []
    assert ProxyHostUpdate().model_dump(exclude_unset=True) == {}
    assert ProxyHostUpdate(locations=[]).locations == []
