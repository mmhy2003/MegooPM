"""Pydantic schemas for the audit log.

:class:`AuditLogRead` is the public projection of an ``AuditLog`` row;
:class:`AuditLogPage` is the paginated envelope returned by the list endpoint
(items plus enough metadata to render page controls).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import AuditAction


class AuditLogRead(BaseModel):
    """Public representation of one audit-log entry."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    actor: str | None
    action: AuditAction
    object_type: str
    object_id: int | None
    meta: dict[str, Any]
    created_at: datetime


class AuditLogPage(BaseModel):
    """A newest-first page of audit entries.

    ``total`` is the count of rows matching the active filters (ignoring the
    pagination window); ``limit``/``offset`` echo the request.
    """

    items: list[AuditLogRead]
    total: int
    limit: int
    offset: int


__all__ = ["AuditLogPage", "AuditLogRead"]
