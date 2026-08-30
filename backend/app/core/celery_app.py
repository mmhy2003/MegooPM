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


# Every node consumes ONE queue of its own, named for its NODE_ID. Reconciles
# are addressed to a specific node's queue rather than broadcast.
#
# This replaces a :class:`~kombu.common.Broadcast` (fanout exchange) queue that
# does not work on the Redis broker: the publish side writes the message into
# each bound worker's ``bcast.<uuid>`` Redis *list*, while kombu's Redis
# transport consumes fanout queues over *pub/sub*. The messages were delivered
# and persisted, and nothing ever read them — reconciles were silently black
# holed, taking the periodic backstop (routed to the same queue) with them.
#
# Direct queues also give a property fanout never could: a reconcile addressed
# to a node that is DOWN waits in Redis and is consumed when that node returns.
NODE_QUEUE_PREFIX = "megoopm.node."


def node_queue(node_id: str) -> str:
    """The name of the queue a given node consumes its own reconciles from."""
    return f"{NODE_QUEUE_PREFIX}{node_id}"


def _configure_ha(celery_app: Celery) -> None:
    """Wire HA config propagation: this node's own queue + a self-scheduled sweep.

    Two paths keep every node's nginx current, and either alone is sufficient:

    * **Push (fast path).** After a successful apply, the applying node enqueues
      one reconcile per live peer, addressed to that peer's queue — see
      :func:`app.tasks.nginx._apply_ha`.
    * **Poll (guarantee).** Every node runs its own beat, which schedules a
      reconcile onto *its own* queue every ``HA_RECONCILE_INTERVAL_SECONDS``.
      Because the route below is built from this process's ``NODE_ID``, a beat
      tick can only ever target the node it runs on. This is what bounds
      convergence for a node that was down, partitioned, or newly added, and it
      is why beat is no longer confined to a single scheduler node — the
      cluster-wide sweeps it also drives stay singletons via ``leader_lock``.

    Reconciles carry an ``expires`` so a node that is offline for a long time
    wakes to a bounded queue rather than a backlog of stale, no-op reconciles.
    """
    from kombu import Queue

    default_queue = celery_app.conf.task_default_queue or "celery"
    own_queue = node_queue(settings.effective_node_id)
    celery_app.conf.task_queues = (Queue(default_queue), Queue(own_queue))
    # Unaddressed reconciles (this node's beat) land on this node's own queue.
    # An explicit ``queue=`` on apply_async overrides this — that is the push path.
    celery_app.conf.task_routes = {
        "app.tasks.nginx.reconcile_local_nginx": {"queue": own_queue},
    }
    celery_app.conf.beat_schedule["reconcile-nginx-across-nodes"] = {
        "task": "app.tasks.nginx.reconcile_local_nginx",
        "schedule": settings.ha_reconcile_interval_seconds,
        "options": {"expires": settings.effective_reconcile_expires_seconds},
    }


celery_app = create_celery()
