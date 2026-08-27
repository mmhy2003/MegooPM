"""Certificate domain services — CRUD and the two creation paths.

Routes stay thin: they call into here with an :class:`AsyncSession` and plain
values. No FastAPI imports (mirrors ``app/services/user.py``).

* :func:`create_custom_certificate` validates uploaded material, persists the
  row (``active``), and writes the material to the shared volume — all before
  the caller commits.
* :func:`create_letsencrypt_certificate` persists a ``pending`` row; the caller
  enqueues the ACME issuance task once the row has an id.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.certificate import Certificate
from app.models.enums import CertificateProvider, CertificateStatus
from app.services.certs import storage
from app.services.certs.acme_client import ChallengeType
from app.services.certs.validation import validate_certificate


class CertificateNotFoundError(Exception):
    """Raised when a certificate id does not exist."""


async def get_certificate(db: AsyncSession, cert_id: int) -> Certificate | None:
    """Return the certificate with ``cert_id`` or ``None``."""
    return await db.get(Certificate, cert_id)


async def list_certificates(db: AsyncSession) -> list[Certificate]:
    """Return all certificates ordered by id."""
    result = await db.execute(select(Certificate).order_by(Certificate.id))
    return list(result.scalars().all())


async def create_custom_certificate(
    db: AsyncSession,
    *,
    name: str,
    certificate_pem: str,
    private_key_pem: str,
    chain_pem: str | None = None,
    certs_dir: str,
) -> Certificate:
    """Validate and store an uploaded certificate; persist an ``active`` row.

    Raises
    :class:`~app.services.certs.validation.CertificateValidationError` if the
    material is invalid — before any row is written. The row is flushed to get an
    id, then material is written to ``{certs_dir}/{id}/``. The caller commits.
    """
    validated = validate_certificate(
        certificate_pem=certificate_pem,
        private_key_pem=private_key_pem,
        chain_pem=chain_pem,
    )

    cert = Certificate(
        name=name,
        provider=CertificateProvider.custom,
        status=CertificateStatus.active,
        domain_names=validated.domain_names,
        expires_on=validated.not_valid_after,
        meta={"uploaded_at": datetime.now(UTC).isoformat()},
    )
    db.add(cert)
    await db.flush()  # populate cert.id for the on-disk path

    storage.write_material(
        certs_dir,
        cert.id,
        fullchain_pem=validated.fullchain_pem,
        privkey_pem=validated.privkey_pem,
        chain_pem=validated.chain_pem or None,
    )
    return cert


async def create_letsencrypt_certificate(
    db: AsyncSession,
    *,
    name: str,
    domain_names: list[str],
    challenge: str = ChallengeType.HTTP_01,
    account_email: str | None = None,
    dns_credential_id: int | None = None,
    dns_provider: str | None = None,
) -> Certificate:
    """Persist a ``pending`` Let's Encrypt certificate row awaiting issuance.

    The actual ACME order runs asynchronously; the caller enqueues the issuance
    task with the returned certificate's id after committing. For DNS-01 the
    saved credential reference (and its provider id, for display) is recorded
    in ``meta`` and resolved again at every issuance/renewal.
    """
    if not domain_names:
        raise ValueError("At least one domain name is required")

    meta: dict = {"challenge": challenge, "account_email": account_email}
    if dns_credential_id is not None:
        meta["dns_credential_id"] = dns_credential_id
        meta["dns_provider"] = dns_provider
    cert = Certificate(
        name=name,
        provider=CertificateProvider.letsencrypt,
        status=CertificateStatus.pending,
        domain_names=domain_names,
        meta=meta,
    )
    db.add(cert)
    await db.flush()
    return cert


async def delete_certificate(db: AsyncSession, cert_id: int, *, certs_dir: str) -> None:
    """Delete a certificate row and its on-disk material.

    Raises :class:`CertificateNotFoundError` if it does not exist. Proxy hosts
    referencing it have their ``certificate_id`` set to NULL by the FK's
    ``ON DELETE SET NULL`` — they revert to plain HTTP on the next reload.
    """
    cert = await db.get(Certificate, cert_id)
    if cert is None:
        raise CertificateNotFoundError(str(cert_id))
    await db.delete(cert)
    await db.flush()
    storage.delete_material(certs_dir, cert_id)


__all__ = [
    "CertificateNotFoundError",
    "create_custom_certificate",
    "create_letsencrypt_certificate",
    "delete_certificate",
    "get_certificate",
    "list_certificates",
]
