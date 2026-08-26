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
TASK_MODULES = ["app.tasks.sample", "app.tasks.nginx", "app.tasks.certs"]


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

    # Scheduled jobs. ``heartbeat`` proves beat is wired; the daily cert sweep
    # (MEG-19) enqueues renewals for Let's Encrypt certs nearing expiry.
    celery_app.conf.beat_schedule = {
        "heartbeat-every-5-minutes": {
            "task": "app.tasks.sample.heartbeat",
            "schedule": crontab(minute="*/5"),
        },
        "renew-due-certificates-daily": {
            "task": "app.tasks.certs.renew_due_certificates",
            "schedule": crontab(
                hour=settings.cert_renew_sweep_hour,
                minute=settings.cert_renew_sweep_minute,
            ),
        },
    }

    if settings.ha_enabled:
        _configure_ha(celery_app)

    return celery_app


# Name of the Celery *broadcast* (fanout) queue used to reload every node's
# local nginx. A Broadcast queue delivers each message to a per-worker queue, so
# every node receives the reconcile — unlike a normal queue (one consumer).
RECONCILE_BROADCAST_QUEUE = "megoopm_reconcile"


def _configure_ha(celery_app: Celery) -> None:
    """Wire HA config propagation: a broadcast reconcile queue + periodic sweep.

    Routing ``reconcile_local_nginx`` to a :class:`~kombu.common.Broadcast`
    queue makes ``.delay()`` fan out to *every* node so each reloads its local
    nginx. The default queue is retained so ordinary tasks keep one-consumer
    semantics. A short periodic reconcile is the self-healing backstop for a
    node that missed a broadcast (was down / partitioned).
    """
    from kombu import Queue
    from kombu.common import Broadcast

    default_queue = celery_app.conf.task_default_queue or "celery"
    celery_app.conf.task_queues = (
        Queue(default_queue),
        Broadcast(RECONCILE_BROADCAST_QUEUE),
    )
    celery_app.conf.task_routes = {
        "app.tasks.nginx.reconcile_local_nginx": {"queue": RECONCILE_BROADCAST_QUEUE},
    }
    celery_app.conf.beat_schedule["reconcile-nginx-across-nodes"] = {
        "task": "app.tasks.nginx.reconcile_local_nginx",
        "schedule": settings.ha_reconcile_interval_seconds,
    }


celery_app = create_celery()
