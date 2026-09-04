"""Validation rules for proxy host location paths (pure pydantic, no DB)."""

from __future__ import annotations

import pytest
from app.models.enums import LocationTarget
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


# --- location targets ---------------------------------------------------------


def test_a_location_defaults_to_a_pool_target() -> None:
    loc = ProxyHostLocationIn(path="/api/", upstream_id=2)
    assert loc.target is LocationTarget.pool


def test_a_host_target_is_inferred_from_the_fields_given() -> None:
    # The frontend sends what the form holds; requiring the target as well
    # would make every existing client's payload invalid.
    loc = ProxyHostLocationIn(path="/api/", forward_host="backend", forward_port=8080)
    assert loc.target is LocationTarget.host


@pytest.mark.parametrize(
    ("payload", "fragment"),
    [
        ({"path": "/a/"}, "either"),
        ({"path": "/a/", "upstream_id": 1, "forward_host": "h", "forward_port": 80}, "either"),
        ({"path": "/a/", "target": "pool"}, "pool"),
        ({"path": "/a/", "target": "host", "forward_host": "h"}, "host and port"),
        ({"path": "/a/", "target": "custom_page"}, "page"),
        ({"path": "/a/", "target": "default_site", "upstream_id": 1}, "no backend"),
        (
            {"path": "/a/", "target": "custom_page", "custom_page_id": 1, "upstream_id": 2},
            "no backend",
        ),
    ],
)
def test_a_location_must_match_its_target(payload: dict, fragment: str) -> None:
    with pytest.raises(ValidationError, match=fragment):
        ProxyHostLocationIn(**payload)


def test_the_answered_targets_need_nothing_but_their_own_field() -> None:
    assert ProxyHostLocationIn(path="/a/", target="default_site").custom_page_id is None
    page = ProxyHostLocationIn(path="/a/", target="custom_page", custom_page_id=4)
    assert page.custom_page_id == 4
