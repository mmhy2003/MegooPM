"""Access-list domain services (basic-auth users + IP allow/deny rules).

Business logic for access lists and their two sub-resources; routes stay thin.
No FastAPI imports — callers pass an :class:`~sqlalchemy.ext.asyncio.AsyncSession`
and plain values, mirroring :mod:`app.services.upstream`.

Basic-auth passwords are hashed into the nginx-native ``$apr1$`` format (see
:mod:`app.services.htpasswd`) before they touch the database; plaintext is never
persisted. The two child collections are eagerly loaded on every read so the
async session never lazy-loads after the request transaction has committed.
Deleting an access list is always allowed: the proxy-host FK is ``SET NULL``, so
attached hosts are simply detached (and re-rendered without their guard).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.access_list import AccessList, AccessListAuth, AccessListClient
from app.services.htpasswd import hash_apr1


class AccessListNotFoundError(Exception):
    """Raised when an access-list id does not exist."""


class AuthUserNotFoundError(Exception):
    """Raised when a basic-auth user id does not exist within the given list."""


class ClientRuleNotFoundError(Exception):
    """Raised when a client-rule id does not exist within the given list."""


class DuplicateUsernameError(Exception):
    """Raised when a username already exists within the access list."""


class MissingPasswordError(Exception):
    """Raised when a replacement introduces a new username with no password.

    The argument is the comma-separated list of offending usernames, so the
    route can name them in the 422 it returns.
    """


async def get_access_list(db: AsyncSession, access_list_id: int) -> AccessList | None:
    """Return the access list (with users and rules) or ``None``."""
    result = await db.execute(
        select(AccessList)
        .where(AccessList.id == access_list_id)
        .options(
            selectinload(AccessList.auth_users),
            selectinload(AccessList.client_rules),
        )
    )
    return result.scalar_one_or_none()


async def list_access_lists(db: AsyncSession) -> list[AccessList]:
    """Return all access lists (with users and rules) ordered by id."""
    result = await db.execute(
        select(AccessList)
        .options(
            selectinload(AccessList.auth_users),
            selectinload(AccessList.client_rules),
        )
        .order_by(AccessList.id)
    )
    return list(result.scalars().all())


async def create_access_list(
    db: AsyncSession,
    *,
    name: str,
    satisfy_any: bool = False,
    pass_auth: bool = False,
    auth_users: list[dict[str, Any]] | None = None,
    clients: list[dict[str, Any]] | None = None,
) -> AccessList:
    """Create an access list, optionally seeding users and rules inline.

    Each seed user's plaintext ``password`` is hashed before persistence. Raises
    :class:`DuplicateUsernameError` if two seed users share a username.
    """
    access_list = AccessList(
        name=name,
        satisfy_any=satisfy_any,
        pass_auth=pass_auth,
        auth_users=[
            AccessListAuth(username=u["username"], password_hash=hash_apr1(u["password"]))
            for u in (auth_users or [])
        ],
        client_rules=[
            AccessListClient(address=c["address"], directive=c["directive"])
            for c in (clients or [])
        ],
    )
    db.add(access_list)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise DuplicateUsernameError(str(exc.orig)) from exc
    refreshed = await get_access_list(db, access_list.id)
    assert refreshed is not None
    return refreshed


def _replace_auth_users(access_list: AccessList, desired: list[dict[str, Any]]) -> None:
    """Make the list's users exactly ``desired``, preserving untouched hashes.

    Rows are matched to the payload by username, which is the only identity a
    client can send back: hashes are never returned, so an entry with no
    ``password`` means "keep this user as they are". Usernames absent from
    ``desired`` are dropped by the relationship's delete-orphan cascade.
    """
    existing = {u.username: u for u in access_list.auth_users}

    # Validate before mutating anything, so a rejected payload leaves the
    # in-session object untouched.
    missing = [
        spec["username"]
        for spec in desired
        if not spec.get("password") and spec["username"] not in existing
    ]
    if missing:
        raise MissingPasswordError(", ".join(missing))

    kept: list[AccessListAuth] = []
    for spec in desired:
        user = existing.get(spec["username"])
        if user is None:
            user = AccessListAuth(
                username=spec["username"], password_hash=hash_apr1(spec["password"])
            )
        elif spec.get("password"):
            user.password_hash = hash_apr1(spec["password"])
        kept.append(user)
    access_list.auth_users = kept


async def update_access_list(
    db: AsyncSession, access_list_id: int, changes: dict[str, Any]
) -> AccessList:
    """Apply a partial update, optionally replacing the users and/or rules.

    ``changes`` may carry ``auth_users`` and ``clients`` as whole-collection
    replacements (see :class:`~app.schemas.access_list.AccessListUpdate`); keys
    that are absent leave their collection alone. Everything lands in a single
    commit so a whole-form save is one transaction and one nginx reload.
    """
    access_list = await get_access_list(db, access_list_id)
    if access_list is None:
        raise AccessListNotFoundError(str(access_list_id))

    changes = dict(changes)
    auth_users = changes.pop("auth_users", None)
    clients = changes.pop("clients", None)

    if auth_users is not None:
        _replace_auth_users(access_list, auth_users)
    if clients is not None:
        # Client rules carry no identity worth preserving and nothing
        # references them, so replacement is a straight rebuild.
        access_list.client_rules = [
            AccessListClient(address=c["address"], directive=c["directive"]) for c in clients
        ]
    for field, value in changes.items():
        setattr(access_list, field, value)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise DuplicateUsernameError(str(exc.orig)) from exc
    refreshed = await get_access_list(db, access_list_id)
    assert refreshed is not None
    return refreshed


async def delete_access_list(db: AsyncSession, access_list_id: int) -> None:
    """Delete an access list (cascading to its users and rules)."""
    access_list = await get_access_list(db, access_list_id)
    if access_list is None:
        raise AccessListNotFoundError(str(access_list_id))
    await db.delete(access_list)
    await db.commit()


# --- Basic-auth user sub-resource ------------------------------------------


async def add_auth_user(
    db: AsyncSession, access_list_id: int, *, username: str, password: str
) -> AccessListAuth:
    """Add a basic-auth user to an access list (password hashed on the way in)."""
    access_list = await get_access_list(db, access_list_id)
    if access_list is None:
        raise AccessListNotFoundError(str(access_list_id))
    user = AccessListAuth(
        access_list_id=access_list_id,
        username=username,
        password_hash=hash_apr1(password),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise DuplicateUsernameError(str(exc.orig)) from exc
    await db.refresh(user)
    return user


async def _get_auth_user(
    db: AsyncSession, access_list_id: int, user_id: int
) -> AccessListAuth | None:
    result = await db.execute(
        select(AccessListAuth).where(
            AccessListAuth.id == user_id,
            AccessListAuth.access_list_id == access_list_id,
        )
    )
    return result.scalar_one_or_none()


async def set_auth_password(
    db: AsyncSession, access_list_id: int, user_id: int, *, password: str
) -> AccessListAuth:
    """Reset a basic-auth user's password."""
    user = await _get_auth_user(db, access_list_id, user_id)
    if user is None:
        raise AuthUserNotFoundError(str(user_id))
    user.password_hash = hash_apr1(password)
    await db.commit()
    await db.refresh(user)
    return user


async def remove_auth_user(db: AsyncSession, access_list_id: int, user_id: int) -> None:
    """Remove a basic-auth user from an access list."""
    user = await _get_auth_user(db, access_list_id, user_id)
    if user is None:
        raise AuthUserNotFoundError(str(user_id))
    await db.delete(user)
    await db.commit()


# --- IP client-rule sub-resource -------------------------------------------


async def add_client_rule(
    db: AsyncSession, access_list_id: int, fields: dict[str, Any]
) -> AccessListClient:
    """Add an allow/deny client rule to an access list."""
    access_list = await get_access_list(db, access_list_id)
    if access_list is None:
        raise AccessListNotFoundError(str(access_list_id))
    rule = AccessListClient(access_list_id=access_list_id, **fields)
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


async def _get_client_rule(
    db: AsyncSession, access_list_id: int, rule_id: int
) -> AccessListClient | None:
    result = await db.execute(
        select(AccessListClient).where(
            AccessListClient.id == rule_id,
            AccessListClient.access_list_id == access_list_id,
        )
    )
    return result.scalar_one_or_none()


async def update_client_rule(
    db: AsyncSession, access_list_id: int, rule_id: int, changes: dict[str, Any]
) -> AccessListClient:
    """Partially update a client rule within an access list."""
    rule = await _get_client_rule(db, access_list_id, rule_id)
    if rule is None:
        raise ClientRuleNotFoundError(str(rule_id))
    for field, value in changes.items():
        setattr(rule, field, value)
    await db.commit()
    await db.refresh(rule)
    return rule


async def remove_client_rule(db: AsyncSession, access_list_id: int, rule_id: int) -> None:
    """Remove a client rule from an access list."""
    rule = await _get_client_rule(db, access_list_id, rule_id)
    if rule is None:
        raise ClientRuleNotFoundError(str(rule_id))
    await db.delete(rule)
    await db.commit()


__all__ = [
    "AccessListNotFoundError",
    "AuthUserNotFoundError",
    "ClientRuleNotFoundError",
    "DuplicateUsernameError",
    "MissingPasswordError",
    "add_auth_user",
    "add_client_rule",
    "create_access_list",
    "delete_access_list",
    "get_access_list",
    "list_access_lists",
    "remove_auth_user",
    "remove_client_rule",
    "set_auth_password",
    "update_access_list",
    "update_client_rule",
]
