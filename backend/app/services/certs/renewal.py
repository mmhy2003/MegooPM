"""Auto-renewal selection: which certificates are due for renewal, and when.

The Celery beat sweep (``app.tasks.certs.renew_due_certificates``) calls
:func:`list_due_certificate_ids` to find the work, then enqueues one renewal task
per id. The date arithmetic lives in the pure :func:`is_due_for_renewal` so it is
trivially unit-testable without a database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.certificate import Certificate
from app.models.enums import CertificateProvider, CertificateStatus


def is_due_for_renewal(
    expires_on: datetime | None,
    *,
    now: datetime,
    before_days: int,
) -> bool:
    """True if a certificate should be renewed now.

    A certificate is due when it has no known expiry yet (never successfully
    issued) or when it expires within ``before_days`` of ``now``.
    """
    if expires_on is None:
        return True
    return expires_on <= now + timedelta(days=before_days)


async def list_due_certificate_ids(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    before_days: int,
) -> list[int]:
    """Return ids of Let's Encrypt certificates due for renewal.

    Only ``letsencrypt`` certificates auto-renew (custom uploads and self-signed
    are the operator's responsibility). ``pending`` rows are excluded — they are
    awaiting their first issuance, handled by the issuance task, not the sweep.
    """
    now = now or datetime.now(UTC)
    stmt = (
        select(Certificate)
        .where(Certificate.provider == CertificateProvider.letsencrypt)
        .where(Certificate.status != CertificateStatus.pending)
        .order_by(Certificate.id)
    )
    certs = (await db.scalars(stmt)).all()
    return [
        c.id
        for c in certs
        if is_due_for_renewal(c.expires_on, now=now, before_days=before_days)
    ]


__all__ = ["is_due_for_renewal", "list_due_certificate_ids"]
