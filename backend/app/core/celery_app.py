"""Celery application factory and configuration.

Celery powers MegooPM's async and scheduled background jobs — later tickets use
it for certificate issuance/renewal and nginx config reloads. The broker and
result backend are Redis, configured from :mod:`app.core.config` so the whole
service shares one source of truth.

Run a worker::

    celery -A app.core.celery_app.celery_app worker --loglevel=info

Run the beat scheduler (periodic jobs)::

    celery -A app.core.celery_app.celery_app beat --loglevel=info
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

# Task modules Celery imports on worker startup so their ``@task`` decorators
# register. Add new task modules here.
TASK_MODULES = ["app.tasks.sample"]


def create_celery() -> Celery:
    """Build and configure the Celery application."""
    celery_app = Celery(
        "megoopm",
        broker=settings.effective_celery_broker_url,
        backend=settings.effective_celery_result_backend,
        include=TASK_MODULES,
    )

    celery_app.conf.update(
        # JSON-only payloads: safe, language-agnostic, no pickle.
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        # Time handling.
        timezone="UTC",
        enable_utc=True,
        # Surface a STARTED state so status lookups distinguish queued vs running.
        task_track_started=True,
        # Reap results after an hour to bound Redis memory.
        result_expires=3600,
        # Local/CI knobs: run inline and persist the eager result so it is
        # retrievable via AsyncResult, mirroring real broker behaviour.
        task_always_eager=settings.celery_task_always_eager,
        task_eager_propagates=settings.celery_task_always_eager,
        task_store_eager_result=True,
        # Avoid noisy startup warnings/retries against a not-yet-ready broker.
        broker_connection_retry_on_startup=True,
    )

    # Scheduled jobs. Real periodic work (cert renewal sweeps, config reloads)
    # is added here by later tickets; ``heartbeat`` proves beat is wired.
    celery_app.conf.beat_schedule = {
        "heartbeat-every-5-minutes": {
            "task": "app.tasks.sample.heartbeat",
            "schedule": crontab(minute="*/5"),
        },
    }

    return celery_app


celery_app = create_celery()
