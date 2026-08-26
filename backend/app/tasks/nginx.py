"""Celery tasks that regenerate/reload nginx from database state.

Two tasks:

* :func:`reload_nginx_config` — the write path. Loads desired state, applies it
  (render → validate → reload) with rollback safety, and — in HA mode — does so
  under a cross-node lock, bumps the shared ``config_version``, and fans a
  reconcile out to every node.
* :func:`reconcile_local_nginx` — the propagation path. Reloads *this* node's
  nginx iff the shared config version is newer than what this node last applied.
  Fanned out on every change and run periodically as a self-healing backstop.

All the transactional safety (locking, validation, rollback) lives in
:func:`app.services.nginx.apply_config`; the HA coordination lives in
:mod:`app.services.cluster`.
"""

from __future__ import annotations

from contextlib import nullcontext

from app.core.celery_app import celery_app
from app.core.config import settings
from app.services.cluster import (
    apply_lock,
    bump_config_version,
    read_config_version,
    read_local_version,
    sync_engine,
    write_local_version,
)
from app.services.nginx import (
    apply_config,
    build_controller,
    load_desired_state_sync,
)


def _apply_single_host() -> dict:
    """Non-HA apply: the original single-host path (local flock, no DB coord)."""
    state = load_desired_state_sync()
    result = apply_config(
        state,
        confd_dir=settings.nginx_confd_dir,
        controller=build_controller(),
        managed_prefix=settings.nginx_managed_prefix,
        stream_dir=settings.nginx_stream_dir,
    )
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
    payload = result.as_dict()
    payload["config_version"] = version
    if result.changed:
        # Tell every *other* node to reload its local nginx from the shared dir.
        reconcile_local_nginx.delay()
    return payload


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
    nothing. Fanned out on every change and scheduled periodically so a node
    that missed the broadcast still converges.
    """
    engine = sync_engine()
    try:
        with engine.connect() as conn:
            version = read_config_version(conn)
    finally:
        engine.dispose()

    local = read_local_version(settings.nginx_reload_marker_path)
    if version <= local:
        return {"reloaded": False, "reason": "already current", "version": version}

    controller = build_controller()
    test = controller.test()
    if not test.ok:
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
    return {
        "reloaded": reload.ok,
        "valid": True,
        "version": version,
        "reload_output": reload.output,
    }


__all__ = ["reload_nginx_config", "reconcile_local_nginx"]
