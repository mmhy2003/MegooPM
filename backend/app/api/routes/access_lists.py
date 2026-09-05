"""Access-list CRUD routes (admin-only).

An access list bundles basic-auth users and IP allow/deny rules that a proxy
host can enforce (attach via ``PATCH /proxy-hosts/{id}`` with ``access_list_id``).
This router exposes full CRUD over lists plus ``/auth-users`` and ``/clients``
sub-resources for managing individual entries. Every mutating write records an
audit entry and enqueues an nginx regenerate-and-reload (see
:mod:`app.api.routes._config_writes`); the reload task id is returned in the
``X-Config-Reload-Task`` response header.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import AdminUser, SessionDep
from app.api.routes._config_writes import after_config_write
from app.models.enums import AuditAction
from app.schemas.access_list import (
    AccessListAuthCreate,
    AccessListAuthRead,
    AccessListAuthUpdate,
    AccessListClientCreate,
    AccessListClientRead,
    AccessListClientUpdate,
    AccessListCreate,
    AccessListRead,
    AccessListUpdate,
)
from app.services import access_list as access_list_service

router = APIRouter(tags=["access-lists"])


@router.get("", response_model=list[AccessListRead])
async def list_access_lists(_admin: AdminUser, db: SessionDep) -> list[AccessListRead]:
    """List all access lists with their users and rules. Admin-only."""
    lists = await access_list_service.list_access_lists(db)
    return [AccessListRead.model_validate(a) for a in lists]


@router.post("", response_model=AccessListRead, status_code=status.HTTP_201_CREATED)
async def create_access_list(
    body: AccessListCreate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> AccessListRead:
    """Create an access list, optionally seeding users and rules inline. Admin-only."""
    try:
        access_list = await access_list_service.create_access_list(
            db,
            name=body.name,
            satisfy_any=body.satisfy_any,
            pass_auth=body.pass_auth,
            auth_users=[u.model_dump() for u in body.auth_users],
            clients=[c.model_dump() for c in body.clients],
        )
    except access_list_service.DuplicateUsernameError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Duplicate username within the access list",
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.create,
        object_type="access_list",
        object_id=access_list.id,
        meta={
            "name": access_list.name,
            "auth_users": len(access_list.auth_users),
            "client_rules": len(access_list.client_rules),
        },
    )
    return AccessListRead.model_validate(access_list)


@router.get("/{access_list_id}", response_model=AccessListRead)
async def get_access_list(access_list_id: int, _admin: AdminUser, db: SessionDep) -> AccessListRead:
    """Fetch a single access list. Admin-only."""
    access_list = await access_list_service.get_access_list(db, access_list_id)
    if access_list is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access list not found")
    return AccessListRead.model_validate(access_list)


@router.patch("/{access_list_id}", response_model=AccessListRead)
async def update_access_list(
    access_list_id: int,
    body: AccessListUpdate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> AccessListRead:
    """Update an access list, optionally replacing its users and/or rules.

    ``auth_users`` and ``clients`` are whole-collection replacements; omitting a
    key leaves that collection alone. A whole-form save therefore costs one
    request, one audit entry and one nginx reload.
    """
    changes = body.model_dump(exclude_unset=True)
    try:
        access_list = await access_list_service.update_access_list(db, access_list_id, changes)
    except access_list_service.AccessListNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Access list not found"
        ) from None
    except access_list_service.MissingPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"A password is required for new user(s): {exc}",
        ) from None
    except access_list_service.DuplicateUsernameError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Duplicate username within the access list",
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.update,
        object_type="access_list",
        object_id=access_list.id,
        meta={"changed": sorted(changes)},
    )
    return AccessListRead.model_validate(access_list)


@router.delete("/{access_list_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_access_list(
    access_list_id: int,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> Response:
    """Delete an access list. Attached hosts are detached (FK SET NULL)."""
    try:
        await access_list_service.delete_access_list(db, access_list_id)
    except access_list_service.AccessListNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Access list not found"
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.delete,
        object_type="access_list",
        object_id=access_list_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=response.headers)


# --- Basic-auth user sub-resource ------------------------------------------


@router.post(
    "/{access_list_id}/auth-users",
    response_model=AccessListAuthRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_auth_user(
    access_list_id: int,
    body: AccessListAuthCreate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> AccessListAuthRead:
    """Add a basic-auth user to an access list. Admin-only."""
    try:
        user = await access_list_service.add_auth_user(
            db, access_list_id, username=body.username, password=body.password
        )
    except access_list_service.AccessListNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Access list not found"
        ) from None
    except access_list_service.DuplicateUsernameError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with that username already exists in the access list",
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.update,
        object_type="access_list",
        object_id=access_list_id,
        meta={"added_auth_user": user.username},
    )
    return AccessListAuthRead.model_validate(user)


@router.patch(
    "/{access_list_id}/auth-users/{user_id}",
    response_model=AccessListAuthRead,
)
async def set_auth_password(
    access_list_id: int,
    user_id: int,
    body: AccessListAuthUpdate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> AccessListAuthRead:
    """Reset a basic-auth user's password. Admin-only."""
    try:
        user = await access_list_service.set_auth_password(
            db, access_list_id, user_id, password=body.password
        )
    except access_list_service.AuthUserNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Auth user not found in this list"
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.update,
        object_type="access_list",
        object_id=access_list_id,
        meta={"reset_password_for": user.username},
    )
    return AccessListAuthRead.model_validate(user)


@router.delete(
    "/{access_list_id}/auth-users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_auth_user(
    access_list_id: int,
    user_id: int,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> Response:
    """Remove a basic-auth user from an access list. Admin-only."""
    try:
        await access_list_service.remove_auth_user(db, access_list_id, user_id)
    except access_list_service.AuthUserNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Auth user not found in this list"
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.update,
        object_type="access_list",
        object_id=access_list_id,
        meta={"removed_auth_user": user_id},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=response.headers)


# --- IP client-rule sub-resource -------------------------------------------


@router.post(
    "/{access_list_id}/clients",
    response_model=AccessListClientRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_client_rule(
    access_list_id: int,
    body: AccessListClientCreate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> AccessListClientRead:
    """Add an allow/deny client rule to an access list. Admin-only."""
    try:
        rule = await access_list_service.add_client_rule(db, access_list_id, body.model_dump())
    except access_list_service.AccessListNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Access list not found"
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.update,
        object_type="access_list",
        object_id=access_list_id,
        meta={"added_client": f"{rule.directive} {rule.address}"},
    )
    return AccessListClientRead.model_validate(rule)


@router.patch(
    "/{access_list_id}/clients/{rule_id}",
    response_model=AccessListClientRead,
)
async def update_client_rule(
    access_list_id: int,
    rule_id: int,
    body: AccessListClientUpdate,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> AccessListClientRead:
    """Update a client rule within an access list. Admin-only."""
    changes = body.model_dump(exclude_unset=True)
    try:
        rule = await access_list_service.update_client_rule(db, access_list_id, rule_id, changes)
    except access_list_service.ClientRuleNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Client rule not found in this list"
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.update,
        object_type="access_list",
        object_id=access_list_id,
        meta={"updated_client": rule_id, "changed": sorted(changes)},
    )
    return AccessListClientRead.model_validate(rule)


@router.delete(
    "/{access_list_id}/clients/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_client_rule(
    access_list_id: int,
    rule_id: int,
    admin: AdminUser,
    db: SessionDep,
    response: Response,
) -> Response:
    """Remove a client rule from an access list. Admin-only."""
    try:
        await access_list_service.remove_client_rule(db, access_list_id, rule_id)
    except access_list_service.ClientRuleNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Client rule not found in this list"
        ) from None
    await after_config_write(
        db,
        response,
        actor=admin,
        action=AuditAction.update,
        object_type="access_list",
        object_id=access_list_id,
        meta={"removed_client": rule_id},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=response.headers)


__all__ = ["router"]
