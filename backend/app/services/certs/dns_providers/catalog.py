"""Provider catalog generated from dns-lexicon by introspection.

Every lexicon provider declares its options in ``Provider.configure_parser``;
we feed each one an ``ArgumentParser`` and read the actions back, so adding a
provider is a dns-lexicon upgrade rather than a code change. Providers whose
optional dependencies are not installed (``find_providers()`` reports
``False``) are omitted, as is the dev-only ``localzone`` provider.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from functools import lru_cache

from lexicon._private.discovery import find_providers, load_provider_module

EXCLUDED_PROVIDERS = frozenset({"localzone"})

# A field is a secret when its value grants access. lexicon's convention is an
# ``auth_`` prefix; the markers catch the stragglers (``api_secret`` etc.).
_SECRET_MARKERS = ("token", "secret", "password", "key")

_LABEL_OVERRIDES: dict[str, str] = {
    "aliyun": "Alibaba Cloud DNS",
    "arvancloud": "ArvanCloud",
    "azure": "Azure DNS",
    "cloudflare": "Cloudflare",
    "cloudns": "ClouDNS",
    "constellix": "Constellix",
    "desec": "deSEC",
    "digitalocean": "DigitalOcean",
    "dnsimple": "DNSimple",
    "dnsmadeeasy": "DNS Made Easy",
    "dnspod": "DNSPod",
    "dreamhost": "DreamHost",
    "duckdns": "Duck DNS",
    "easydns": "easyDNS",
    "gandi": "Gandi",
    "godaddy": "GoDaddy",
    "googleclouddns": "Google Cloud DNS",
    "henet": "Hurricane Electric",
    "hetzner": "Hetzner",
    "hostingde": "hosting.de",
    "infomaniak": "Infomaniak",
    "inwx": "INWX",
    "ionos": "IONOS",
    "linode": "Linode (legacy API)",
    "linode4": "Linode",
    "luadns": "LuaDNS",
    "namecheap": "Namecheap",
    "namecom": "Name.com",
    "namesilo": "NameSilo",
    "nfsn": "NearlyFreeSpeech",
    "nsone": "NS1",
    "ovh": "OVH",
    "powerdns": "PowerDNS",
    "rfc2136": "RFC 2136 (dynamic update)",
    "route53": "AWS Route 53",
    "sakuracloud": "Sakura Cloud",
    "transip": "TransIP",
    "ultradns": "UltraDNS",
    "vercel": "Vercel",
    "vultr": "Vultr",
    "yandexcloud": "Yandex Cloud",
}


class UnknownDnsProviderError(ValueError):
    """``provider_id`` is not in the catalog."""


@dataclass(frozen=True)
class DnsProviderField:
    name: str
    label: str
    help: str
    secret: bool


@dataclass(frozen=True)
class DnsProviderInfo:
    id: str
    label: str
    description: str
    fields: tuple[DnsProviderField, ...]

    def field(self, name: str) -> DnsProviderField | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None


def is_secret_field(name: str) -> bool:
    """Whether a provider option holds credential material."""
    return name.startswith("auth_") or any(marker in name for marker in _SECRET_MARKERS)


def humanize(name: str) -> str:
    """``auth_token`` -> ``Auth token``."""
    return name.replace("_", " ").capitalize()


def provider_label(provider_id: str) -> str:
    return _LABEL_OVERRIDES.get(provider_id) or provider_id.capitalize()


def _fields_from_parser(parser: argparse.ArgumentParser) -> tuple[DnsProviderField, ...]:
    fields: list[DnsProviderField] = []
    for action in parser._actions:  # noqa: SLF001 - argparse has no public accessor
        if not action.option_strings or action.dest == "help":
            continue
        fields.append(
            DnsProviderField(
                name=action.dest,
                label=humanize(action.dest),
                help=" ".join((action.help or "").split()),
                secret=is_secret_field(action.dest),
            )
        )
    return tuple(fields)


def _build_info(provider_id: str) -> DnsProviderInfo | None:
    try:
        module = load_provider_module(provider_id)
    except Exception:  # noqa: BLE001 - a broken provider must not sink the catalog
        return None
    parser = argparse.ArgumentParser(prog=provider_id, add_help=False)
    module.Provider.configure_parser(parser)
    return DnsProviderInfo(
        id=provider_id,
        label=provider_label(provider_id),
        description=" ".join((parser.description or "").split()),
        fields=_fields_from_parser(parser),
    )


@lru_cache(maxsize=1)
def list_providers() -> tuple[DnsProviderInfo, ...]:
    """Every usable provider, sorted by label."""
    infos: list[DnsProviderInfo] = []
    for provider_id, available in find_providers().items():
        if not available or provider_id in EXCLUDED_PROVIDERS:
            continue
        info = _build_info(provider_id)
        if info is not None:
            infos.append(info)
    return tuple(sorted(infos, key=lambda i: i.label.lower()))


def get_provider(provider_id: str) -> DnsProviderInfo:
    for info in list_providers():
        if info.id == provider_id:
            return info
    raise UnknownDnsProviderError(f"Unknown DNS provider {provider_id!r}")


__all__ = [
    "EXCLUDED_PROVIDERS",
    "DnsProviderField",
    "DnsProviderInfo",
    "UnknownDnsProviderError",
    "get_provider",
    "humanize",
    "is_secret_field",
    "list_providers",
    "provider_label",
]
