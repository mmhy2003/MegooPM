"""``DnsProvider`` backed by dns-lexicon.

One lexicon ``Client`` call per record operation; the client authenticates on
entry and cleans up on exit. The zone lexicon needs (``domain``) is the
registered domain of the record name, derived offline from the bundled public
suffix list. Errors are wrapped so callers (and the audit/last_error fields)
never see credential values.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import tldextract
from lexicon.client import Client

# Offline extractor: never fetch the public suffix list at runtime.
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), fallback_to_snapshot=True)


class DnsProviderError(RuntimeError):
    """A DNS provider call failed. The message is safe to store and display."""


def zone_for(name: str) -> str:
    """Registered domain (zone) for a record name, e.g. ``example.co.uk``."""
    zone = _EXTRACT(name.rstrip(".")).top_domain_under_public_suffix
    if not zone:
        raise DnsProviderError(f"Cannot determine the DNS zone for {name!r}")
    return zone


def scrub(message: str, secrets: Iterable[str]) -> str:
    """Replace every secret value (4+ chars) in ``message`` with ``***``."""
    for secret in secrets:
        if secret and len(secret) >= 4:
            message = message.replace(secret, "***")
    return message


class LexiconDnsProvider:
    """Sets/removes ``_acme-challenge`` TXT records through a lexicon provider."""

    def __init__(
        self,
        provider_id: str,
        options: dict[str, str],
        *,
        client_factory: Callable[[dict[str, Any]], Any] = Client,
    ) -> None:
        self.provider_id = provider_id
        self._options = dict(options)
        self._client_factory = client_factory

    def _config(self, zone: str) -> dict[str, Any]:
        return {
            "provider_name": self.provider_id,
            "domain": zone,
            self.provider_id: dict(self._options),
        }

    def _run(self, name: str, operation: Callable[[Any, str], None]) -> None:
        fqdn = name.rstrip(".") + "."
        try:
            with self._client_factory(self._config(zone_for(name))) as ops:
                operation(ops, fqdn)
        except DnsProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - every provider/HTTP error becomes one type
            raise DnsProviderError(
                f"{self.provider_id}: {scrub(str(exc), self._options.values())}"
            ) from exc

    def set_txt_record(self, name: str, value: str) -> None:
        self._run(name, lambda ops, fqdn: ops.create_record("TXT", fqdn, value))

    def remove_txt_record(self, name: str, value: str) -> None:
        self._run(name, lambda ops, fqdn: ops.delete_record(rtype="TXT", name=fqdn, content=value))


__all__ = ["DnsProviderError", "LexiconDnsProvider", "scrub", "zone_for"]
