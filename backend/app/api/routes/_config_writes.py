"""Shared side effects for config-affecting writes (proxy hosts & upstreams).

Every successful mutation to a proxy host or upstream pool must do two things
beyond persisting the row:

1. **Record an audit entry** — who changed which object, and how.
2. **Drive the config engine** — enqueue the async regenerate-and-reload task so
   the rendered ``upstream {}`` / ``server {}`` blocks converge to the new state.

:func:`after_config_write` centralises both so the CRUD handlers stay thin and
consistent. It returns the reload task id, which handlers surface in the
``X-Config-Reload-Task`` response header for clients that want to poll
``GET /tasks/{id}`` for the :class:`ApplyResult`.

The mutation itself is already committed by the service layer before this runs;
the audit row is written in its own short transaction. Reload enqueuing is the
last step so a queueing hiccup never rolls back a persisted change.
"""

from __future__ import annotations

from typing import Any

from fastapi import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AuditAction
from app.models.user import User
from app.services.audit import record_audit
from app.services.tasks import enqueue_nginx_reload

RELOAD_TASK_HEADER = "X-Config-Reload-Task"


async def after_config_write(
    db: AsyncSession,
    response: Response,
    *,
    actor: User,
    action: AuditAction,
    object_type: str,
    object_id: int | None,
    meta: dict[str, Any] | None = None,
) -> str:
    """Record an audit row and enqueue an nginx reload; return the task id.

    Sets the ``X-Config-Reload-Task`` header on ``response`` so the caller can
    poll the reload's outcome.
    """
    await record_audit(
        db,
        actor=actor.email,
        action=action,
        object_type=object_type,
        object_id=object_id,
        meta=meta,
    )
    await db.commit()

    task = enqueue_nginx_reload()
    response.headers[RELOAD_TASK_HEADER] = task.task_id
    return task.task_id


__all__ = ["after_config_write", "RELOAD_TASK_HEADER"]
