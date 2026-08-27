"""LexiconDnsProvider maps our DnsProvider protocol onto dns-lexicon's Client."""

from __future__ import annotations

import pytest
from app.services.certs.dns_providers.lexicon_provider import (
    DnsProviderError,
    LexiconDnsProvider,
    scrub,
    zone_for,
)


class _FakeOps:
    def __init__(self, log: list, fail_with: Exception | None) -> None:
        self._log = log
        self._fail = fail_with

    def create_record(self, rtype: str, name: str, content: str) -> bool:
        if self._fail:
            raise self._fail
        self._log.append(("create", rtype, name, content))
        return True

    def delete_record(self, identifier=None, rtype=None, name=None, content=None) -> bool:
        if self._fail:
            raise self._fail
        self._log.append(("delete", rtype, name, content))
        return True


class _FakeClientFactory:
    """Stands in for ``lexicon.client.Client``: records configs, yields fake ops."""

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.configs: list[dict] = []
        self.log: list = []
        self._fail = fail_with

    def __call__(self, config: dict):
        self.configs.append(config)
        factory = self

        class _Ctx:
            def __enter__(self_inner):
                return _FakeOps(factory.log, factory._fail)

            def __exit__(self_inner, *exc):
                return False

        return _Ctx()


def test_zone_for_uses_public_suffix_list() -> None:
    assert zone_for("_acme-challenge.www.example.co.uk") == "example.co.uk"
    assert zone_for("_acme-challenge.example.com.") == "example.com"
    with pytest.raises(DnsProviderError):
        zone_for("localhost")


def test_set_txt_record_builds_lexicon_config_and_fqdn() -> None:
    factory = _FakeClientFactory()
    provider = LexiconDnsProvider(
        "cloudflare", {"auth_token": "cf-secret-token", "zone_id": "z1"}, client_factory=factory
    )

    provider.set_txt_record("_acme-challenge.www.example.com", "validation-value")

    assert factory.configs == [
        {
            "provider_name": "cloudflare",
            "domain": "example.com",
            "cloudflare": {"auth_token": "cf-secret-token", "zone_id": "z1"},
        }
    ]
    assert factory.log == [
        ("create", "TXT", "_acme-challenge.www.example.com.", "validation-value")
    ]


def test_remove_txt_record_deletes_by_type_name_and_content() -> None:
    factory = _FakeClientFactory()
    provider = LexiconDnsProvider("hetzner", {"auth_token": "hz-token"}, client_factory=factory)

    provider.remove_txt_record("_acme-challenge.example.org.", "v")

    assert factory.log == [("delete", "TXT", "_acme-challenge.example.org.", "v")]


def test_provider_errors_are_wrapped_and_scrubbed() -> None:
    factory = _FakeClientFactory(fail_with=RuntimeError("401 for token cf-secret-token"))
    provider = LexiconDnsProvider(
        "cloudflare", {"auth_token": "cf-secret-token"}, client_factory=factory
    )

    with pytest.raises(DnsProviderError) as excinfo:
        provider.set_txt_record("_acme-challenge.example.com", "v")

    message = str(excinfo.value)
    assert message.startswith("cloudflare: ")
    assert "cf-secret-token" not in message
    assert "***" in message


def test_scrub_ignores_very_short_values() -> None:
    assert scrub("id 42 token abcdef", ["42", "abcdef"]) == "id 42 token ***"


def test_real_lexicon_client_receives_provider_options(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: ``Client(dict)`` is lexicon's *legacy* flat-config path, which
    silently ignores the nested ``{provider: {...}}`` block and sent
    ``Authorization: Bearer None`` to Cloudflare (400 / code 6111)."""
    import requests

    seen: list[dict] = []

    def fake_request(action, url, params=None, data=None, headers=None, **_):
        seen.append({"action": action, "url": url, "headers": headers})
        raise RuntimeError("stop before any network call")

    monkeypatch.setattr(requests, "request", fake_request)
    provider = LexiconDnsProvider("cloudflare", {"auth_token": "cf-secret-token"})

    with pytest.raises(DnsProviderError):
        provider.set_txt_record("_acme-challenge.example.com", "v")

    assert seen, "lexicon never issued a request"
    assert seen[0]["headers"]["Authorization"] == "Bearer cf-secret-token"
