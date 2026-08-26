"""Stream (TCP/UDP forward) CRUD routes (admin-only).

A stream forwards a raw TCP and/or UDP port to a backend ``host:port``. This
router exposes full CRUD; every mutating write records an audit entry and
enqueues an nginx regenerate-and-reload (see
:mod:`app.api.routes._config_writes`), returning the reload task id in the
``X-Config-Reload-Task`` header. A create/update whose ``incoming_port`` is
already claimed is rejected with 409; a bad certificate reference with 422.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import AdminUser, SessionDep
from app.api.routes._config_writes import after_config_write
from app.models.enums import AuditAction
from app.schemas.stream import StreamCreate, StreamRead, StreamUpdate
from app.services import stream as stream_service

router = APIRouter(tags=["streams"])


@router.get("", response_model=list[StreamRead])
async def list_streams(_admin: AdminUser, db: SessionDep) -> list[StreamRead]:
    """List all streams. Admin-only."""
    streams = await stream_service.list_streams(db)
    return [StreamRead.model_validate(s) for s in streams]


@router.post("", response_model=StreamRead, status_code=status.HTTP_201_CREATED)
async def create_stream(
    body: StreamCreate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> StreamRead:
    """Create a TCP/UDP stream forward. Admin-only."""
    try:
        stream = await stream_service.create_stream(db, body.model_dump())
    except stream_service.PortConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except stream_service.InvalidReferenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.create,
        object_type="stream",
        object_id=stream.id,
        meta={
            "incoming_port": stream.incoming_port,
            "forward": f"{stream.forward_host}:{stream.forward_port}",
        },
    )
    return StreamRead.model_validate(stream)


@router.get("/{stream_id}", response_model=StreamRead)
async def get_stream(stream_id: int, _admin: AdminUser, db: SessionDep) -> StreamRead:
    """Fetch a single stream. Admin-only."""
    stream = await stream_service.get_stream(db, stream_id)
    if stream is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found")
    return StreamRead.model_validate(stream)


@router.patch("/{stream_id}", response_model=StreamRead)
async def update_stream(
    stream_id: int,
    body: StreamUpdate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> StreamRead:
    """Update a stream. Admin-only."""
    changes = body.model_dump(exclude_unset=True)
    try:
        stream = await stream_service.update_stream(db, stream_id, changes)
    except stream_service.StreamNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found"
        ) from None
    except stream_service.PortConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except stream_service.InvalidReferenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.update,
        object_type="stream",
        object_id=stream.id,
        meta={"changed": sorted(changes)},
    )
    return StreamRead.model_validate(stream)


@router.delete("/{stream_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_stream(
    stream_id: int,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> Response:
    """Delete a stream. Admin-only."""
    try:
        await stream_service.delete_stream(db, stream_id)
    except stream_service.StreamNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found"
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.delete,
        object_type="stream",
        object_id=stream_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=response.headers)


__all__ = ["router"]
