"""The DNS provider catalog is generated from dns-lexicon by introspection."""

from __future__ import annotations

import pytest
from app.services.certs.dns_providers import catalog


def test_catalog_is_large_and_sorted_by_label() -> None:
    providers = catalog.list_providers()
    assert len(providers) >= 50
    labels = [p.label.lower() for p in providers]
    assert labels == sorted(labels)


def test_cloudflare_fields_and_secret_flags() -> None:
    cf = catalog.get_provider("cloudflare")
    assert cf.label == "Cloudflare"
    assert "Global API key" in cf.description or "API token" in cf.description
    names = {f.name: f for f in cf.fields}
    assert names["auth_token"].secret is True
    assert names["auth_username"].secret is True  # starts with auth_
    assert names["zone_id"].secret is False
    assert names["auth_token"].label == "Auth token"
    assert "help" not in names  # argparse's own -h/--help action is never a field
    assert cf.field("auth_token") is names["auth_token"]
    assert cf.field("nope") is None


def test_dev_only_and_unavailable_providers_are_excluded() -> None:
    ids = {p.id for p in catalog.list_providers()}
    assert "localzone" not in ids
    assert "oci" not in ids  # its extra is not installed
    assert {"cloudflare", "route53", "digitalocean", "hetzner", "powerdns"} <= ids


@pytest.fixture
def fresh_catalog():
    catalog.list_providers.cache_clear()
    yield
    catalog.list_providers.cache_clear()


def test_reads_the_provider_list_of_lexicon_3_26(
    fresh_catalog: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 3.26 returns a sorted list of names and reports a provider's missing
    # extras separately, instead of a {name: available} dict.
    monkeypatch.setattr(catalog, "find_providers", lambda: ["cloudflare", "localzone", "oci"])
    monkeypatch.setattr(
        catalog, "find_missing_extras", lambda p: ["oci"] if p == "oci" else [], raising=False
    )
    assert [p.id for p in catalog.list_providers()] == ["cloudflare"]


def test_reads_the_provider_dict_of_older_lexicon(
    fresh_catalog: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        catalog,
        "find_providers",
        lambda: {"cloudflare": True, "localzone": True, "oci": False},
    )
    assert [p.id for p in catalog.list_providers()] == ["cloudflare"]


def test_unknown_provider_raises() -> None:
    with pytest.raises(catalog.UnknownDnsProviderError):
        catalog.get_provider("not-a-provider")


@pytest.mark.parametrize(
    ("name", "secret"),
    [
        ("auth_token", True),
        ("auth_username", True),
        ("api_secret", True),
        ("password", True),
        ("private_key", True),
        ("zone_id", False),
        ("pdns_server", False),
        ("ttl", False),
    ],
)
def test_is_secret_field(name: str, secret: bool) -> None:
    assert catalog.is_secret_field(name) is secret


def test_labels() -> None:
    assert catalog.humanize("auth_token") == "Auth token"
    assert catalog.humanize("pdns_server_id") == "Pdns server id"
    assert catalog.provider_label("route53") == "AWS Route 53"
    assert catalog.provider_label("digitalocean") == "DigitalOcean"
    assert catalog.provider_label("zonomi") == "Zonomi"  # title-case fallback


def test_settings_defaults() -> None:
    from app.core.config import settings

    assert settings.acme_dns_propagation_timeout_seconds == 120
    assert settings.acme_dns_propagation_interval_seconds == 5
    assert settings.acme_dns_propagation_settle_seconds == 10
