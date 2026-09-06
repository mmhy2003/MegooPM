"""The maintenance allow-list, where it is validated.

Only the schema lives here. The route tests sit beside the other settings
routes in ``test_settings_api.py``: they need a real Postgres and the stubbed
config reload that suite already sets up.
"""

from __future__ import annotations

import pytest
from app.schemas.proxy_host import ProxyHostCreate, ProxyHostUpdate
from pydantic import ValidationError


def test_the_allow_list_takes_addresses_and_ranges() -> None:
    host = ProxyHostCreate(
        domain_names=["a.example.com"],
        upstream_id=1,
        maintenance_enabled=True,
        maintenance_allow=["203.0.113.5", "10.0.0.0/8", "2001:db8::/32"],
    )

    assert host.maintenance_allow == ["203.0.113.5", "10.0.0.0/8", "2001:db8::/32"]


@pytest.mark.parametrize("bad", ["not-an-ip", "999.1.1.1", "10.0.0.0/99", ""])
def test_an_entry_that_is_not_an_address_is_rejected(bad: str) -> None:
    # nginx refuses to load a geo block containing this, and that failure takes
    # the whole edge down — the API has to catch it before it reaches the file.
    with pytest.raises(ValidationError, match="address"):
        ProxyHostCreate(
            domain_names=["a.example.com"],
            upstream_id=1,
            maintenance_enabled=True,
            maintenance_allow=[bad],
        )


def test_a_partial_update_validates_the_allow_list_too() -> None:
    # The edit path is the one an operator actually uses; unvalidated here, the
    # create-time check protects nothing.
    with pytest.raises(ValidationError, match="address"):
        ProxyHostUpdate(maintenance_allow=["not-an-ip"])


def test_surrounding_whitespace_is_stripped() -> None:
    # A pasted address keeps its spaces, and " 203.0.113.5 0;" is a geo block
    # nginx will not load.
    host = ProxyHostCreate(
        domain_names=["a.example.com"], upstream_id=1, maintenance_allow=[" 203.0.113.5 "]
    )

    assert host.maintenance_allow == ["203.0.113.5"]
