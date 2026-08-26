"""Schemas for the background-task endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SampleTaskRequest(BaseModel):
    """Payload for enqueuing the sample ``add`` task."""

    x: int = Field(default=0)
    y: int = Field(default=0)


class TaskEnqueued(BaseModel):
    """Returned when a task is accepted onto the queue."""

    task_id: str
    status: str


class TaskStatus(BaseModel):
    """A serializable view of a Celery task's state and result."""

    task_id: str
    # Celery state name: PENDING, STARTED, SUCCESS, FAILURE, RETRY, REVOKED.
    status: str
    # True once the task has finished (successfully or not).
    ready: bool
    # Present only on success; shape depends on the task.
    result: Any | None = None
    # Present only on failure: a string rendering of the exception.
    error: str | None = None
