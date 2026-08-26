"""Sample and scheduled Celery tasks.

These prove the async infrastructure end to end: ``add`` is an enqueueable task
with a retrievable result, and ``heartbeat`` is wired into Celery beat as a
periodic job. Feature tickets add real tasks (certificate issuance/renewal,
nginx config reloads) alongside these and remove ``add`` once it is no longer a
useful smoke check.
"""

from __future__ import annotations

from app.core.celery_app import celery_app


@celery_app.task(name="app.tasks.sample.add")
def add(x: int, y: int) -> int:
    """Return ``x + y``. A trivial enqueueable task for smoke-testing."""
    return x + y


@celery_app.task(name="app.tasks.sample.heartbeat")
def heartbeat() -> str:
    """Scheduled no-op proving Celery beat can run periodic jobs."""
    return "ok"
