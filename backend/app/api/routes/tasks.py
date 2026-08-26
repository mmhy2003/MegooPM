"""Background-task endpoints.

``POST /tasks/sample`` enqueues the sample ``add`` task (a demonstration and QA
hook); ``GET /tasks/{task_id}`` returns its status and result. Real
task-triggering endpoints (cert issuance, nginx reloads) follow the same
pattern: a thin route that delegates to :mod:`app.services.tasks`.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.schemas.tasks import SampleTaskRequest, TaskEnqueued, TaskStatus
from app.services.tasks import enqueue_sample_add, get_task_status

router = APIRouter(tags=["tasks"])


@router.post(
    "/tasks/sample",
    response_model=TaskEnqueued,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_sample(payload: SampleTaskRequest) -> TaskEnqueued:
    """Enqueue the sample ``add`` task; returns a task id to poll."""
    return enqueue_sample_add(payload.x, payload.y)


@router.get("/tasks/{task_id}", response_model=TaskStatus)
async def task_status(task_id: str) -> TaskStatus:
    """Return the status (and result, once ready) of a background task."""
    return get_task_status(task_id)
