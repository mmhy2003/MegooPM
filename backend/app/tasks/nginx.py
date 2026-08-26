"""Celery task that regenerates and reloads nginx from database state.

The task is the async, observable seam demanded by MEG-16: proxy-host writes
enqueue :func:`reload_nginx_config`, and callers poll ``GET /tasks/{id}`` for a
structured :class:`~app.services.nginx.engine.ApplyResult` payload.

It is deliberately thin — load state, apply, return the result dict. All the
transactional safety (locking, validation, rollback) lives in
:func:`app.services.nginx.apply_config`.
"""

from __future__ import annotations

from app.core.celery_app import celery_app
from app.core.config import settings
from app.services.nginx import (
    apply_config,
    build_controller,
    load_desired_state_sync,
)


@celery_app.task(name="app.tasks.nginx.reload_nginx_config")
def reload_nginx_config() -> dict:
    """Rebuild nginx config from the DB, validate, and reload. Returns a result.

    The return value is the JSON-serialisable
    :meth:`ApplyResult.as_dict` payload — ``changed``, ``valid``, ``reloaded``,
    ``rolled_back``, ``message`` and command output — retrievable via the task
    status endpoint.
    """
    state = load_desired_state_sync()
    result = apply_config(
        state,
        confd_dir=settings.nginx_confd_dir,
        controller=build_controller(),
        managed_prefix=settings.nginx_managed_prefix,
        stream_dir=settings.nginx_stream_dir,
    )
    return result.as_dict()


__all__ = ["reload_nginx_config"]
