"""DNS provider catalog + saved credentials for DNS-01 (admin-only).

* ``GET  /dns-providers``                  — generated provider catalog.
* ``GET  /dns-credentials``                — saved credentials (secrets: names only).
* ``POST /dns-credentials``                — save a credential set.
* ``PATCH /dns-credentials/{id}``          — rename / replace options.
* ``POST /dns-credentials/{id}/verify``    — probe: set + remove a TXT record.
* ``DELETE /dns-credentials/{id}``         — 409 while certificates reference it.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminUser, SessionDep
from app.models.dns_credential import DnsProviderCredential
from app.models.enums import AuditAction
from app.models.user import User
from app.schemas.dns_credential import (
    CertificateRef,
    DnsCredentialCreate,
    DnsCredentialRead,
    DnsCredentialUpdate,
    DnsCredentialVerified,
    DnsCredentialVerify,
    DnsProviderFieldRead,
    DnsProviderInfoRead,
)
from app.services.audit import record_audit
from app.services.certs import dns_credentials
from app.services.certs.dns_providers.catalog import (
    UnknownDnsProviderError,
    list_providers,
    provider_label,
)
from app.services.certs.dns_providers.lexicon_provider import DnsProviderError

router = APIRouter(tags=["dns-providers"])

VERIFY_TIMEOUT_SECONDS = 30


async def _get_or_404(db: AsyncSession, credential_id: int) -> DnsProviderCredential:
    credential = await dns_credentials.get_credential(db, credential_id)
    if credential is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="DNS credential not found"
        )
    return credential


async def _read(db: AsyncSession, credential: DnsProviderCredential) -> DnsCredentialRead:
    using = await dns_credentials.certificates_using(db, credential.id)
    return DnsCredentialRead(
        id=credential.id,
        name=credential.name,
        provider=credential.provider,
        provider_label=provider_label(credential.provider),
        options=dict(credential.options or {}),
        secret_fields=dns_credentials.secret_field_names(credential),
        in_use_by=[CertificateRef(id=c.id, name=c.name) for c in using],
        created_at=credential.created_at,
        updated_at=credential.updated_at,
    )


async def _audit(
    db: AsyncSession,
    *,
    actor: User,
    action: AuditAction,
    object_id: int,
    meta: dict[str, Any],
) -> None:
    await record_audit(
        db,
        actor=actor.email,
        action=action,
        object_type="dns_credential",
        object_id=object_id,
        meta=meta,
    )
    await db.commit()


def _unprocessable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


def _duplicate() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="A DNS credential with that name already exists",
    )


@router.get("/dns-providers", response_model=list[DnsProviderInfoRead])
async def list_dns_providers(_admin: AdminUser) -> list[DnsProviderInfoRead]:
    """The generated dns-lexicon provider catalog. Admin-only."""
    return [
        DnsProviderInfoRead(
            id=info.id,
            label=info.label,
            description=info.description,
            fields=[
                DnsProviderFieldRead(name=f.name, label=f.label, help=f.help, secret=f.secret)
                for f in info.fields
            ],
        )
        for info in list_providers()
    ]


@router.get("/dns-credentials", response_model=list[DnsCredentialRead])
async def list_dns_credentials(_admin: AdminUser, db: SessionDep) -> list[DnsCredentialRead]:
    """Saved credentials with their usage. Admin-only."""
    return [await _read(db, c) for c in await dns_credentials.list_credentials(db)]


@router.post(
    "/dns-credentials", response_model=DnsCredentialRead, status_code=status.HTTP_201_CREATED
)
async def create_dns_credential(
    body: DnsCredentialCreate, admin: AdminUser, db: SessionDep
) -> DnsCredentialRead:
    """Save a credential set. 422 on unknown provider/field or no secret; 409 on duplicate name."""
    try:
        credential = await dns_credentials.create_credential(
            db, name=body.name, provider=body.provider, options=body.options
        )
    except (UnknownDnsProviderError, ValueError) as exc:
        raise _unprocessable(exc) from None
    except dns_credentials.DuplicateCredentialNameError:
        raise _duplicate() from None
    await _audit(
        db,
        actor=admin,
        action=AuditAction.create,
        object_id=credential.id,
        meta={
            "name": credential.name,
            "provider": credential.provider,
            "fields": sorted(
                {*credential.options, *dns_credentials.secret_field_names(credential)}
            ),
        },
    )
    return await _read(db, credential)


@router.patch("/dns-credentials/{credential_id}", response_model=DnsCredentialRead)
async def update_dns_credential(
    credential_id: int, body: DnsCredentialUpdate, admin: AdminUser, db: SessionDep
) -> DnsCredentialRead:
    """Rename and/or replace options (blank secrets keep their value). Admin-only."""
    credential = await _get_or_404(db, credential_id)
    old_name = credential.name
    try:
        credential = await dns_credentials.update_credential(
            db, credential, name=body.name, options=body.options
        )
    except (UnknownDnsProviderError, ValueError) as exc:
        raise _unprocessable(exc) from None
    except dns_credentials.DuplicateCredentialNameError:
        raise _duplicate() from None
    meta: dict[str, Any] = {}
    if credential.name != old_name:
        meta["changes"] = {"name": [old_name, credential.name]}
    if body.options is not None:
        meta["fields"] = sorted(k for k, v in body.options.items() if (v or "").strip())
    await _audit(db, actor=admin, action=AuditAction.update, object_id=credential.id, meta=meta)
    return await _read(db, credential)


@router.post("/dns-credentials/{credential_id}/verify", response_model=DnsCredentialVerified)
async def verify_dns_credential(
    credential_id: int, body: DnsCredentialVerify, _admin: AdminUser, db: SessionDep
) -> DnsCredentialVerified:
    """Write and remove a probe TXT record with the real provider (30 s cap)."""
    credential = await _get_or_404(db, credential_id)
    try:
        await asyncio.wait_for(
            run_in_threadpool(dns_credentials.verify_credential, credential, body.domain),
            timeout=VERIFY_TIMEOUT_SECONDS,
        )
    except DnsProviderError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    except TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{credential.provider}: timed out after {VERIFY_TIMEOUT_SECONDS}s",
        ) from None
    return DnsCredentialVerified()


@router.delete("/dns-credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dns_credential(credential_id: int, admin: AdminUser, db: SessionDep) -> None:
    """Delete a credential set; 409 while certificates still reference it."""
    credential = await _get_or_404(db, credential_id)
    snapshot = {"name": credential.name, "provider": credential.provider}
    try:
        await dns_credentials.delete_credential(db, credential)
    except dns_credentials.CredentialInUseError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    await _audit(db, actor=admin, action=AuditAction.delete, object_id=credential_id, meta=snapshot)
