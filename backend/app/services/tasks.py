"""Background-task services.

Business logic for enqueuing tasks and looking up their status. Wraps Celery's
``AsyncResult`` so Celery types never leak into the API layer.
"""

from __future__ import annotations

from typing import Any

from app.core.celery_app import celery_app
from app.schemas.tasks import TaskEnqueued, TaskStatus
from app.tasks.sample import add


def enqueue_sample_add(x: int, y: int) -> TaskEnqueued:
    """Enqueue the sample ``add`` task and return its handle."""
    async_result = add.delay(x, y)
    return TaskEnqueued(task_id=async_result.id, status=async_result.status)


def get_task_status(task_id: str) -> TaskStatus:
    """Look up a task's state and result by id.

    Unknown ids report ``PENDING`` — Celery cannot distinguish a never-seen id
    from one that is still queued, since neither has a stored result yet.
    """
    async_result = celery_app.AsyncResult(task_id)

    result: Any | None = None
    error: str | None = None
    if async_result.successful():
        result = async_result.result
    elif async_result.failed():
        error = str(async_result.result)

    return TaskStatus(
        task_id=task_id,
        status=async_result.status,
        ready=async_result.ready(),
        result=result,
        error=error,
    )
