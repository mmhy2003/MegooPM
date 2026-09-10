"""nginx config/reload endpoints (admin-only).

``POST /nginx/reload`` enqueues the async regenerate-and-reload Celery task and
returns a task id to poll via ``GET /tasks/{task_id}`` — the observable status
seam. ``GET /nginx/preview`` renders (without applying) the config the engine
would write for the current database state.

Proxy-host CRUD (a separate ticket) calls the same reload service after a
successful write so config regenerates automatically; this router exposes the
manual trigger and the preview.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import AdminUser, SessionDep
from app.models.cluster_state import CLUSTER_STATE_ROW_ID, ClusterState
from app.schemas.nginx import NginxApplyStatus, NginxConfigFile, NginxConfigPreview
from app.schemas.tasks import TaskEnqueued
from app.services.nginx import load_desired_state, render_config, render_stream_config
from app.services.tasks import enqueue_nginx_reload

router = APIRouter(tags=["nginx"])


@router.post(
    "/reload",
    response_model=TaskEnqueued,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reload_nginx(_admin: AdminUser) -> TaskEnqueued:
    """Enqueue a config regeneration + nginx reload; returns a task id to poll."""
    return enqueue_nginx_reload()


@router.get("/preview", response_model=NginxConfigPreview)
async def preview_nginx_config(_admin: AdminUser, db: SessionDep) -> NginxConfigPreview:
    """Render the config for current DB state without writing or reloading."""
    state = await load_desired_state(db)
    # HTTP-context files plus the top-level stream{} files, in stable name order.
    rendered = {**render_config(state), **render_stream_config(state)}
    return NginxConfigPreview(
        files=[NginxConfigFile(name=name, content=rendered[name]) for name in sorted(rendered)]
    )



@router.get("/status", response_model=NginxApplyStatus)
async def nginx_apply_status(_admin: AdminUser, db: SessionDep) -> NginxApplyStatus:
    """How the most recent apply went, with nginx's own words if it failed.

    The UI's failure banner reads this. A failed ``nginx -t`` is rolled back,
    and the host that caused it stays in the database — so until it is fixed,
    every later change rolls back too. That is worth a standing banner, and
    this is the one place it is kept.
    """
    row = await db.get(ClusterState, CLUSTER_STATE_ROW_ID)
    if row is None:
        return NginxApplyStatus()
    return NginxApplyStatus(
        ok=row.last_apply_ok,
        at=row.last_apply_at,
        message=row.last_apply_message,
        output=row.last_apply_output,
    )


__all__ = ["router"]
