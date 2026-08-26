"""Audit-log domain services.

Two responsibilities live here:

- :func:`record_audit` — the reusable *write path*. Privileged mutation handlers
  call it to append one immutable row describing what changed and who changed it.
- :func:`list_audit_logs` — the *read path* backing ``GET /audit-log``: a
  newest-first, filtered, paginated query.

No FastAPI imports — callers pass an :class:`~sqlalchemy.ext.asyncio.AsyncSession`
and plain values, mirroring ``app/services/user.py``.

``record_audit`` deliberately does **not** commit. It ``add``s the row (and
``flush``es to populate its id) so that the audit entry participates in the
caller's transaction: the mutation and its audit record commit atomically, or
roll back together. The mutation handler owns the ``commit``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.enums import AuditAction


async def record_audit(
    session: AsyncSession,
    *,
    actor: str | None,
    action: AuditAction,
    object_type: str,
    object_id: int | None = None,
    meta: dict[str, Any] | None = None,
) -> AuditLog:
    """Append one audit-log row within the caller's transaction.

    ``actor`` is the authenticated principal (e.g. the user's email) or ``None``
    for system-initiated changes. ``action`` is one of :class:`AuditAction`.
    ``object_type``/``object_id`` loosely reference the mutated row (no FK, so
    history survives deletion). ``meta`` holds structured details / a before-after
    diff.

    The row is added and flushed (so ``entry.id`` is available) but **not**
    committed — the caller commits alongside the mutation it is recording.
    """
    entry = AuditLog(
        actor=actor,
        action=action,
        object_type=object_type,
        object_id=object_id,
        meta=meta or {},
    )
    session.add(entry)
    await session.flush()
    return entry


async def list_audit_logs(
    session: AsyncSession,
    *,
    object_type: str | None = None,
    object_id: int | None = None,
    actor: str | None = None,
    action: AuditAction | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AuditLog], int]:
    """Return a newest-first page of audit rows plus the total match count.

    Filters are combined with ``AND``; any left ``None`` is not applied. The
    returned ``total`` reflects the filters but not the pagination window, so
    callers can render page counts.
    """
    filters = []
    if object_type is not None:
        filters.append(AuditLog.object_type == object_type)
    if object_id is not None:
        filters.append(AuditLog.object_id == object_id)
    if actor is not None:
        filters.append(AuditLog.actor == actor)
    if action is not None:
        filters.append(AuditLog.action == action)

    total = await session.scalar(
        select(func.count()).select_from(AuditLog).where(*filters)
    )

    result = await session.execute(
        select(AuditLog)
        .where(*filters)
        # Newest first; id breaks ties for rows sharing a created_at instant.
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = list(result.scalars().all())
    return rows, int(total or 0)


__all__ = ["list_audit_logs", "record_audit"]
