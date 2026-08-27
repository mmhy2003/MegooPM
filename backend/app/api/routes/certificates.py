"""Certificate management endpoints (admin-only).

* ``GET    /certificates``            — list all certificates.
* ``GET    /certificates/{id}``       — one certificate.
* ``POST   /certificates/custom``     — upload + validate a custom certificate.
* ``POST   /certificates/letsencrypt``— request a Let's Encrypt certificate
                                         (async ACME issuance, returns a task id).
* ``POST   /certificates/{id}/renew`` — enqueue renewal/re-issuance.
* ``DELETE /certificates/{id}``       — delete a certificate and its material.

Private key material is accepted on upload but never returned. Every mutation is
recorded in the audit log. Issuance/renewal run as tracked Celery tasks, so the
frontend polls ``GET /tasks/{id}`` for progress.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import AdminUser, SessionDep
from app.core.config import settings
from app.models.enums import AuditAction
from app.schemas.certificate import (
    CertificateIssued,
    CertificateRead,
    CustomCertificateCreate,
    LetsEncryptCertificateCreate,
)
from app.services import audit as audit_service
from app.services import tasks as task_service
from app.services.certs import dns_credentials
from app.services.certs import service as cert_service
from app.services.certs.acme_client import ChallengeType
from app.services.certs.validation import CertificateValidationError

router = APIRouter(tags=["certificates"])


@router.get("", response_model=list[CertificateRead])
async def list_certificates(_admin: AdminUser, db: SessionDep) -> list[CertificateRead]:
    """List all certificates. Admin-only."""
    certs = await cert_service.list_certificates(db)
    return [CertificateRead.model_validate(c) for c in certs]


@router.get("/{cert_id}", response_model=CertificateRead)
async def get_certificate(cert_id: int, _admin: AdminUser, db: SessionDep) -> CertificateRead:
    """Return one certificate. Admin-only."""
    cert = await cert_service.get_certificate(db, cert_id)
    if cert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Certificate not found")
    return CertificateRead.model_validate(cert)


@router.post("/custom", response_model=CertificateRead, status_code=status.HTTP_201_CREATED)
async def upload_custom_certificate(
    payload: CustomCertificateCreate, admin: AdminUser, db: SessionDep
) -> CertificateRead:
    """Validate and store an uploaded custom certificate. Admin-only."""
    try:
        cert = await cert_service.create_custom_certificate(
            db,
            name=payload.name,
            certificate_pem=payload.certificate_pem,
            private_key_pem=payload.private_key_pem,
            chain_pem=payload.chain_pem,
            certs_dir=settings.nginx_certs_dir,
        )
    except CertificateValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    await audit_service.record_audit(
        db,
        actor=admin.email,
        action=AuditAction.create,
        object_type="certificate",
        object_id=cert.id,
        meta={"provider": "custom", "domain_names": cert.domain_names},
    )
    await db.commit()
    await db.refresh(cert)
    return CertificateRead.model_validate(cert)


@router.post("/letsencrypt", response_model=CertificateIssued, status_code=status.HTTP_202_ACCEPTED)
async def request_letsencrypt_certificate(
    payload: LetsEncryptCertificateCreate, admin: AdminUser, db: SessionDep
) -> CertificateIssued:
    """Create a pending Let's Encrypt cert and enqueue ACME issuance. Admin-only.

    DNS-01 requires saved DNS provider credentials (``dns_credential_id``);
    HTTP-01 must not carry one. Both are validated before the row is created.
    """
    dns_provider: str | None = None
    if payload.challenge == ChallengeType.DNS_01:
        if payload.dns_credential_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="DNS-01 requires dns_credential_id (saved DNS provider credentials)",
            )
        credential = await dns_credentials.get_credential(db, payload.dns_credential_id)
        if credential is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown DNS credential"
            )
        dns_provider = credential.provider
    elif payload.dns_credential_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="dns_credential_id is only valid with the dns-01 challenge",
        )
    cert = await cert_service.create_letsencrypt_certificate(
        db,
        name=payload.name,
        domain_names=payload.domain_names,
        challenge=payload.challenge,
        account_email=payload.account_email,
        dns_credential_id=payload.dns_credential_id,
        dns_provider=dns_provider,
    )
    await audit_service.record_audit(
        db,
        actor=admin.email,
        action=AuditAction.create,
        object_type="certificate",
        object_id=cert.id,
        meta={
            "provider": "letsencrypt",
            "domain_names": cert.domain_names,
            "challenge": payload.challenge,
            "dns_provider": dns_provider,
        },
    )
    await db.commit()
    await db.refresh(cert)

    enqueued = task_service.enqueue_cert_issue(cert.id)
    return CertificateIssued(
        certificate=CertificateRead.model_validate(cert),
        task_id=enqueued.task_id,
        task_status=enqueued.status,
    )


@router.post(
    "/{cert_id}/renew",
    response_model=CertificateIssued,
    status_code=status.HTTP_202_ACCEPTED,
)
async def renew_certificate(cert_id: int, admin: AdminUser, db: SessionDep) -> CertificateIssued:
    """Enqueue renewal/re-issuance for a certificate. Admin-only."""
    cert = await cert_service.get_certificate(db, cert_id)
    if cert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Certificate not found")

    await audit_service.record_audit(
        db,
        actor=admin.email,
        action=AuditAction.update,
        object_type="certificate",
        object_id=cert.id,
        meta={"action": "renew"},
    )
    await db.commit()

    enqueued = task_service.enqueue_cert_renew(cert.id)
    return CertificateIssued(
        certificate=CertificateRead.model_validate(cert),
        task_id=enqueued.task_id,
        task_status=enqueued.status,
    )


@router.delete("/{cert_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_certificate(cert_id: int, admin: AdminUser, db: SessionDep) -> None:
    """Delete a certificate and its on-disk material. Admin-only."""
    try:
        await cert_service.delete_certificate(db, cert_id, certs_dir=settings.nginx_certs_dir)
    except cert_service.CertificateNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Certificate not found"
        ) from exc

    await audit_service.record_audit(
        db,
        actor=admin.email,
        action=AuditAction.delete,
        object_type="certificate",
        object_id=cert_id,
    )
    await db.commit()


__all__ = ["router"]
