"""Celery tasks for certificate issuance, renewal, and the auto-renew sweep.

These are the tracked, observable seam the issue asks for: requesting a Let's
Encrypt certificate (or renewing one) runs here and its progress is retrievable
via ``GET /tasks/{id}``. On success a certificate's material lands on the shared
volume and an nginx reload is enqueued so the new cert is served immediately.

Like the nginx reload task, these run outside FastAPI's event loop, so they open
their own short-lived async session via :func:`asyncio.run`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.certificate import Certificate
from app.models.enums import CertificateStatus
from app.services.certs import dns_credentials
from app.services.certs.acme_client import ChallengeType
from app.services.certs.issuance import build_issuer, issue_for_certificate
from app.services.certs.renewal import list_due_certificate_ids


def _mark_failed(cert: Certificate, exc: Exception) -> None:
    cert.status = CertificateStatus.failed
    cert.meta = {
        **(cert.meta or {}),
        "last_error": str(exc),
        "failed_at": datetime.now(UTC).isoformat(),
    }


async def _issue_async(cert_id: int, *, session_factory: async_sessionmaker | None = None) -> dict:
    """Issue ``cert_id``. ``session_factory`` is injectable for tests; production
    opens its own engine (Celery runs outside FastAPI's session scope)."""
    engine = None
    if session_factory is None:
        engine = create_async_engine(settings.database_url, poolclass=NullPool)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            cert = await session.get(Certificate, cert_id)
            if cert is None:
                return {"cert_id": cert_id, "issued": False, "error": "not found"}
            try:
                dns_provider = None
                if (cert.meta or {}).get("challenge") == ChallengeType.DNS_01:
                    dns_provider = await dns_credentials.build_provider_for(session, cert)
                issuer = build_issuer(cert, dns_provider=dns_provider)
                issue_for_certificate(cert, issuer=issuer, certs_dir=settings.nginx_certs_dir)
                await session.commit()
            except Exception as exc:  # noqa: BLE001 - persist the failure, then surface it
                if cert.status != CertificateStatus.failed:
                    _mark_failed(cert, exc)  # errors raised before issue_for_certificate
                await session.commit()  # keep status=failed + last_error
                return {
                    "cert_id": cert_id,
                    "issued": False,
                    "error": str(exc),
                    "status": "failed",
                }
            return {
                "cert_id": cert_id,
                "issued": True,
                "status": str(cert.status),
                "expires_on": cert.expires_on.isoformat() if cert.expires_on else None,
                "domain_names": list(cert.domain_names),
            }
    finally:
        if engine is not None:
            await engine.dispose()


@celery_app.task(name="app.tasks.certs.issue_certificate")
def issue_certificate(cert_id: int) -> dict:
    """Issue (or re-issue) the certificate row ``cert_id`` and reload nginx.

    Returns a JSON-serialisable summary. A successful issuance enqueues an nginx
    reload so the freshly issued material is served without a manual trigger.
    """
    result = asyncio.run(_issue_async(cert_id))
    if result.get("issued"):
        # Import here to avoid a task-module import cycle at load time.
        from app.tasks.nginx import reload_nginx_config

        reload_nginx_config.delay()
    return result


@celery_app.task(name="app.tasks.certs.renew_certificate")
def renew_certificate(cert_id: int) -> dict:
    """Renew a certificate — identical to issuance, re-run against a live row."""
    return issue_certificate(cert_id)


async def _due_ids_async() -> list[int]:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            return await list_due_certificate_ids(
                session,
                now=datetime.now(UTC),
                before_days=settings.cert_renew_before_days,
            )
    finally:
        await engine.dispose()


def _enqueue_due_renewals() -> dict:
    due = asyncio.run(_due_ids_async())
    for cert_id in due:
        renew_certificate.delay(cert_id)
    return {"due_count": len(due), "cert_ids": due}


@celery_app.task(name="app.tasks.certs.renew_due_certificates")
def renew_due_certificates() -> dict:
    """Beat sweep: enqueue a renewal for every Let's Encrypt cert near expiry.

    In HA mode every node runs beat, so this task is emitted once per node. Two
    guards, and only the second is sufficient on its own:

    * a cluster-wide **leader lock**, which excludes nodes sweeping at the same
      instant;
    * a **sweep claim**, which excludes nodes sweeping in quick succession.

    The lock alone is not enough, and that distinction is the whole point: it is
    held only for the milliseconds it takes to read the due list and enqueue, so
    a second node's beat firing a fraction of a second later finds it free and
    re-enqueues every certificate the first node's renewals have not yet marked.
    Each duplicate drives another ACME order against Let's Encrypt's
    five-duplicates-per-week ceiling, and two nodes issuing the same certificate
    concurrently race to write the same files on the shared mount.

    Outside HA mode there is one beat and no shared database, so it runs
    unguarded.
    """
    if not settings.ha_enabled:
        return _enqueue_due_renewals()

    # Imported lazily so the single-host path has no DB-coordination dependency.
    from app.services.cluster import claim_sweep, leader_lock, sync_engine

    engine = sync_engine()
    try:
        lock_file = f"{settings.ha_lock_dir}/leader-cert-renew-sweep.lock"
        with leader_lock(engine, "cert-renew-sweep", lock_file=lock_file) as is_leader:
            if not is_leader:
                return {"skipped": True, "reason": "another node holds the renewal lock"}
            # Its own transaction: the claim must commit even though the leader
            # lock's connection stays open for the enqueue below.
            with engine.begin() as conn:
                claimed = claim_sweep(
                    conn,
                    "cert-renew-sweep",
                    min_interval_seconds=settings.cert_renew_sweep_min_interval_seconds,
                )
            if not claimed:
                return {"skipped": True, "reason": "already swept this period"}
            return _enqueue_due_renewals()
    finally:
        engine.dispose()


__all__ = [
    "issue_certificate",
    "renew_certificate",
    "renew_due_certificates",
]
