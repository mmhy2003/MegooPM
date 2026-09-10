"""Celery tasks that regenerate/reload nginx from database state.

Two tasks:

* :func:`reload_nginx_config` — the write path. Loads desired state, applies it
  (render → validate → reload) with rollback safety, and — in HA mode — does so
  under a cross-node lock, bumps the shared ``config_version``, and pushes a
  reconcile onto each live peer's own queue.
* :func:`reconcile_local_nginx` — the propagation path. Reloads *this* node's
  nginx iff the shared config version is newer than what this node last applied.

Propagation has a fast path and a guarantee, and only the guarantee is load
bearing: the push above is best-effort, while every node's own beat schedules a
reconcile onto its own queue every ``HA_RECONCILE_INTERVAL_SECONDS``. That bound
holds for a node that was down, partitioned, or newly added — cases where no
push was ever delivered.

All the transactional safety (locking, validation, rollback) lives in
:func:`app.services.nginx.apply_config`; the HA coordination lives in
:mod:`app.services.cluster`.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import nullcontext
from datetime import UTC, datetime

from app.core.celery_app import celery_app, node_queue
from app.core.config import settings
from app.schemas.events import Event
from app.services.cluster import (
    apply_lock,
    bump_config_version,
    live_peers,
    read_config_version,
    read_local_version,
    record_node_state,
    sync_engine,
    write_local_version,
)
from app.services.events import publish
from app.services.nginx import (
    apply_config,
    build_controller,
    load_desired_state_sync,
)
from app.services.nginx.apply_status import ApplyOutcome, outcome_of, record_apply_outcome
from app.services.nginx.engine import ApplyResult

log = logging.getLogger(__name__)


def _record_outcome(outcome: ApplyOutcome) -> None:
    """Keep the outcome where the admin UI can read it. Never raises.

    Advisory, like the node registry: it describes what nginx did and changes
    none of it, so a write failure must not turn a finished apply into a failed
    task. It is logged, because a stale "healthy" is the one lie this row can
    tell.
    """
    engine = sync_engine()
    try:
        with engine.begin() as conn:
            record_apply_outcome(conn, outcome)
    except Exception as exc:  # noqa: BLE001 - advisory data only
        log.warning("could not record the nginx apply outcome: %s", exc)
    finally:
        engine.dispose()


def _announce(event_type: str) -> None:
    """Tell connected browsers an apply finished, so the banner updates at once.

    Never raises: an announcement is not worth failing the apply it describes.
    In a worker this task is synchronous and the loader's own event loop has
    finished before this runs, so ``asyncio.run`` nests nothing. A running loop
    here means the task was called inline from async code — Celery's eager
    mode — where starting another is refused; the banner still catches up on
    its next fetch, so the announcement is simply skipped.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass  # no loop running: the normal worker case
    else:
        log.debug("skipping %s announcement: called inside a running event loop", event_type)
        return
    try:
        asyncio.run(publish(Event(type=event_type, at=datetime.now(UTC))))
    except Exception as exc:  # noqa: BLE001 - announcing must not break the apply
        log.warning("could not announce %s: %s", event_type, exc)


def _finish(result: ApplyResult) -> None:
    """Record, log and announce how an apply went.

    The log line is its own, deliberately: the task's return value also carries
    the output, but Celery logs a result truncated to ~1024 characters, and the
    managed-file list comes first — so on any real instance the reason was cut
    off exactly where it began.
    """
    outcome = outcome_of(result)
    if not outcome.ok:
        log.warning("nginx apply rolled back: %s\n%s", outcome.message, outcome.output)
    _record_outcome(outcome)
    _announce("config.applied" if outcome.ok else "config.failed")


def _apply_single_host() -> dict:
    """Non-HA apply: the original single-host path (local flock, no DB coord)."""
    state = load_desired_state_sync()
    result = apply_config(
        state,
        confd_dir=settings.nginx_confd_dir,
        controller=build_controller(),
        managed_prefix=settings.nginx_managed_prefix,
        stream_dir=settings.nginx_stream_dir,
        default_dir=settings.nginx_default_dir,
    )
    _finish(result)
    return result.as_dict()


def _apply_ha() -> dict:
    """HA apply: cross-node advisory lock + shared config-version bump + fan-out.

    The advisory lock, the render/validate/reload, and the version bump all
    happen inside one transaction, so no other node can interleave a write and
    the version is a faithful monotonic record of applied config sets.
    """
    state = load_desired_state_sync()
    engine = sync_engine()
    try:
        with apply_lock(engine, lock_file=f"{settings.ha_lock_dir}/nginx-apply.lock") as conn:
            result = apply_config(
                state,
                confd_dir=settings.nginx_confd_dir,
                controller=build_controller(),
                managed_prefix=settings.nginx_managed_prefix,
                stream_dir=settings.nginx_stream_dir,
                default_dir=settings.nginx_default_dir,
                lock=nullcontext(),  # the advisory lock already serialises us
            )
            version = (
                bump_config_version(conn, node_id=settings.effective_node_id)
                if result.changed
                else read_config_version(conn)
            )
    finally:
        engine.dispose()

    # This node already reloaded (or was already current) inside apply_config.
    write_local_version(settings.nginx_reload_marker_path, version)
    _record_state(version)
    _finish(result)
    payload = result.as_dict()
    payload["config_version"] = version
    if result.changed:
        payload["notified"] = _push_reconcile_to_peers()
    return payload


def _push_reconcile_to_peers() -> list[str]:
    """Enqueue a reconcile onto each live peer's own queue; return their ids.

    This is the low-latency path only. It is deliberately best-effort: a peer
    that is absent from the registry, or a broker hiccup here, costs latency and
    nothing else, because every node's own beat reconciles it within
    ``HA_RECONCILE_INTERVAL_SECONDS`` regardless. So a failure to notify must
    never fail the apply that already succeeded.
    """
    engine = sync_engine()
    try:
        with engine.connect() as conn:
            peers = live_peers(
                conn,
                exclude=settings.effective_node_id,
                max_age_seconds=settings.node_liveness_window_seconds,
            )
    except Exception:  # noqa: BLE001 - the apply is already committed; never fail it
        return []
    finally:
        engine.dispose()

    notified: list[str] = []
    for peer in peers:
        try:
            reconcile_local_nginx.apply_async(
                queue=node_queue(peer),
                expires=settings.effective_reconcile_expires_seconds,
            )
            notified.append(peer)
        except Exception:  # noqa: BLE001 - one unreachable peer must not stop the rest
            continue
    return notified


@celery_app.task(name="app.tasks.nginx.reload_nginx_config")
def reload_nginx_config() -> dict:
    """Rebuild nginx config from the DB, validate, and reload. Returns a result.

    The return value is the JSON-serialisable :meth:`ApplyResult.as_dict`
    payload (plus ``config_version`` in HA mode), retrievable via the task
    status endpoint.
    """
    if settings.ha_enabled:
        return _apply_ha()
    return _apply_single_host()


@celery_app.task(name="app.tasks.nginx.reconcile_local_nginx")
def reconcile_local_nginx() -> dict:
    """Reload this node's nginx iff the shared config version is newer.

    The shared ``conf.d`` volume already holds the file bytes (written by
    whichever node applied the change), so this only has to reload the local
    nginx process. Idempotent: a node already at the current version does
    nothing.

    Reached two ways: pushed onto this node's queue by whichever node applied a
    change, and scheduled onto its own queue by this node's beat every
    ``HA_RECONCILE_INTERVAL_SECONDS``. The scheduled path is the guarantee — it
    is what converges a node that was down, partitioned, or newly added, for
    which no push was ever delivered.

    Every run records this node's position in ``cluster_node``, which is both the
    fan-out target list and the cluster's convergence view.
    """
    engine = sync_engine()
    try:
        with engine.connect() as conn:
            version = read_config_version(conn)
    finally:
        engine.dispose()

    local = read_local_version(settings.nginx_reload_marker_path)
    if version <= local:
        _record_state(local)
        return {"reloaded": False, "reason": "already current", "version": version}

    controller = build_controller()
    test = controller.test()
    if not test.ok:
        # Report the version this node is actually serving, not the one it failed
        # to reach — otherwise the convergence view would claim it caught up.
        _record_state(local)
        return {
            "reloaded": False,
            "valid": False,
            "reason": "shared config failed nginx -t",
            "version": version,
            "test_output": test.output,
        }
    reload = controller.reload()
    if reload.ok:
        write_local_version(settings.nginx_reload_marker_path, version)
    _record_state(version if reload.ok else local)
    return {
        "reloaded": reload.ok,
        "valid": True,
        "version": version,
        "reload_output": reload.output,
    }


def _record_state(applied_version: int) -> None:
    """Best-effort heartbeat into the node registry.

    Never raises: the registry is advisory (see :mod:`app.services.cluster.nodes`),
    so a write failure here must not turn a successful reload into a failed task.
    """
    engine = sync_engine()
    try:
        with engine.begin() as conn:
            record_node_state(conn, settings.effective_node_id, applied_version)
    except Exception:  # noqa: BLE001 - advisory data only
        pass
    finally:
        engine.dispose()


__all__ = ["reload_nginx_config", "reconcile_local_nginx"]
