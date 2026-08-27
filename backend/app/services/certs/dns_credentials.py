"""Reusable DNS provider credentials (DNS-01).

Secrets leave this module in the clear only through :func:`decrypted_options`
/ :func:`build_provider`, which the issuance path and the verify probe call.
API reads use :func:`secret_field_names` (names only).
"""

from __future__ import annotations

import json
import logging
import secrets as _secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import SecretDecryptError, decrypt_secret, encrypt_secret
from app.models.certificate import Certificate
from app.models.dns_credential import DnsProviderCredential
from app.services.certs.acme_client import DnsProviderNotConfigured
from app.services.certs.dns_providers.catalog import get_provider
from app.services.certs.dns_providers.lexicon_provider import LexiconDnsProvider

logger = logging.getLogger(__name__)


class CredentialInUseError(Exception):
    """Deleting a credential that certificates still reference."""

    def __init__(self, certificate_names: list[str]) -> None:
        super().__init__("Still used by: " + ", ".join(certificate_names))
        self.certificate_names = certificate_names


class DuplicateCredentialNameError(Exception):
    """A credential with that name already exists."""


def split_options(
    provider_id: str, options: dict[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Validate ``options`` against the catalog and split into (public, secret).

    Values are trimmed and blank values dropped (so a blank secret on update
    means "unchanged"). Raises ``UnknownDnsProviderError`` for an unknown
    provider and ``ValueError`` for a field the provider does not declare.
    """
    info = get_provider(provider_id)
    public: dict[str, str] = {}
    secret: dict[str, str] = {}
    for key, raw in options.items():
        field = info.field(key)
        if field is None:
            raise ValueError(f"Unknown field {key!r} for provider {provider_id!r}")
        value = (raw or "").strip()
        if not value:
            continue
        (secret if field.secret else public)[key] = value
    return public, secret


def _secrets_of(credential: DnsProviderCredential) -> dict[str, str]:
    try:
        return json.loads(decrypt_secret(credential.secrets_enc))
    except SecretDecryptError:
        logger.warning("DNS credential %s cannot be decrypted (secret_key changed?)", credential.id)
        return {}


def secret_field_names(credential: DnsProviderCredential) -> list[str]:
    return sorted(_secrets_of(credential))


def decrypted_options(credential: DnsProviderCredential) -> dict[str, str]:
    return {**(credential.options or {}), **_secrets_of(credential)}


async def list_credentials(db: AsyncSession) -> list[DnsProviderCredential]:
    result = await db.execute(select(DnsProviderCredential).order_by(DnsProviderCredential.name))
    return list(result.scalars().all())


async def get_credential(db: AsyncSession, credential_id: int) -> DnsProviderCredential | None:
    return await db.get(DnsProviderCredential, credential_id)


async def get_by_name(db: AsyncSession, name: str) -> DnsProviderCredential | None:
    result = await db.execute(
        select(DnsProviderCredential).where(DnsProviderCredential.name == name)
    )
    return result.scalar_one_or_none()


async def create_credential(
    db: AsyncSession, *, name: str, provider: str, options: dict[str, str]
) -> DnsProviderCredential:
    public, secret = split_options(provider, options)
    if not secret:
        raise ValueError("At least one secret credential field is required")
    if await get_by_name(db, name) is not None:
        raise DuplicateCredentialNameError(name)
    credential = DnsProviderCredential(
        name=name,
        provider=provider,
        options=public,
        secrets_enc=encrypt_secret(json.dumps(secret)),
    )
    db.add(credential)
    await db.commit()
    await db.refresh(credential)
    return credential


async def update_credential(
    db: AsyncSession,
    credential: DnsProviderCredential,
    *,
    name: str | None = None,
    options: dict[str, str] | None = None,
) -> DnsProviderCredential:
    """Rename and/or replace options. Supplied secrets replace; omitted/blank stay."""
    if name is not None and name != credential.name:
        if await get_by_name(db, name) is not None:
            raise DuplicateCredentialNameError(name)
        credential.name = name
    if options is not None:
        public, secret = split_options(credential.provider, options)
        credential.options = public
        credential.secrets_enc = encrypt_secret(json.dumps({**_secrets_of(credential), **secret}))
    await db.commit()
    await db.refresh(credential)
    return credential


async def certificates_using(db: AsyncSession, credential_id: int) -> list[Certificate]:
    """Certificates whose ``meta.dns_credential_id`` references this credential."""
    result = await db.execute(
        select(Certificate)
        .where(Certificate.meta["dns_credential_id"].as_integer() == credential_id)
        .order_by(Certificate.id)
    )
    return list(result.scalars().all())


async def delete_credential(db: AsyncSession, credential: DnsProviderCredential) -> None:
    using = await certificates_using(db, credential.id)
    if using:
        raise CredentialInUseError([c.name for c in using])
    await db.delete(credential)
    await db.commit()


def build_provider(credential: DnsProviderCredential) -> LexiconDnsProvider:
    return LexiconDnsProvider(credential.provider, decrypted_options(credential))


async def build_provider_for(db: AsyncSession, certificate: Certificate) -> LexiconDnsProvider:
    """Resolve ``certificate.meta['dns_credential_id']`` into a ready provider."""
    credential_id = (certificate.meta or {}).get("dns_credential_id")
    if credential_id is None:
        raise DnsProviderNotConfigured(
            "Certificate has no DNS credential; choose one for the DNS-01 challenge."
        )
    credential = await get_credential(db, int(credential_id))
    if credential is None:
        raise DnsProviderNotConfigured(f"DNS credential {credential_id} no longer exists")
    return build_provider(credential)


def verify_credential(credential: DnsProviderCredential, domain: str) -> None:
    """Prove the credential can write to ``domain``'s zone: set then remove a probe TXT.

    Blocking (talks to the provider API); callers run it in a thread. Raises
    ``DnsProviderError`` (already scrubbed) on failure.
    """
    provider = build_provider(credential)
    name = f"_megoopm-verify.{domain.strip().rstrip('.')}"
    value = f"megoopm-{_secrets.token_hex(8)}"
    provider.set_txt_record(name, value)
    provider.remove_txt_record(name, value)


__all__ = [
    "CredentialInUseError",
    "DuplicateCredentialNameError",
    "build_provider",
    "build_provider_for",
    "certificates_using",
    "create_credential",
    "decrypted_options",
    "delete_credential",
    "get_by_name",
    "get_credential",
    "list_credentials",
    "secret_field_names",
    "split_options",
    "update_credential",
    "verify_credential",
]
