"""API keys: minting, verifying, and the rows.

Where this feature's security properties live. Nothing here imports FastAPI —
the dependency layer turns a rejection into a status code, and this module
decides only what a rejection *is*.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import ApiKey
from app.models.user import User

TOKEN_PREFIX = "mgm_"
#: "mgm_" plus eight characters of the secret: 72 bits, so the unique index is
#: a lookup handle rather than a collision risk.
PREFIX_LEN = 12
MAX_KEYS = 20
#: A write per request, to store a timestamp nobody reads to the second, would
#: double the database cost of a read-only API call.
TOUCH_INTERVAL = timedelta(minutes=5)


class KeyRejected(Exception):
    """Base: this token does not authenticate anyone."""


class UnknownKey(KeyRejected):
    """No such prefix, or the digest did not match."""


class KeyDisabled(KeyRejected):
    """The owner switched it off."""


class KeyExpired(KeyRejected):
    """Past ``expires_at``."""


class ApiKeyLimitReached(Exception):
    """``MAX_KEYS`` already exist for this user."""


def _as_utc(value: datetime) -> datetime:
    """Read a stored timestamp as UTC.

    SQLite (the test fixture) hands back a naive datetime for a
    ``DateTime(timezone=True)`` column; Postgres hands back an aware one.
    Comparing the naive one against ``datetime.now(UTC)`` raises TypeError, so
    every comparison in this module goes through here.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def hash_token(token: str) -> str:
    """SHA-256 hex of the whole token. See the model docstring for why not Argon2."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_token() -> tuple[str, str, str]:
    """Return ``(token, prefix, digest)``. The token itself is never stored."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return token, token[:PREFIX_LEN], hash_token(token)


async def create(
    db: AsyncSession, user: User, *, name: str, expires_at: datetime | None
) -> tuple[ApiKey, str]:
    """Mint one key. The token is returned once and is never obtainable again."""
    count = await db.scalar(
        select(func.count()).select_from(ApiKey).where(ApiKey.user_id == user.id)
    )
    if (count or 0) >= MAX_KEYS:
        raise ApiKeyLimitReached

    # Read before the loop: a rollback expires every object in the session, so
    # reading it on the retry would be lazy IO in a plain expression — which
    # raises MissingGreenlet rather than reloading.
    user_id = user.id

    # Twelve base64 characters is 72 bits, so a prefix collision is not a
    # practical event — but the index is unique, and "cannot happen" is how a
    # 500 reaches a user once every few years. One retry costs nothing.
    for attempt in range(2):
        token, prefix, digest = generate_token()
        row = ApiKey(
            user_id=user_id,
            name=name,
            token_prefix=prefix,
            token_hash=digest,
            expires_at=expires_at,
        )
        db.add(row)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            if attempt:
                raise
            continue
        await db.refresh(row)
        return row, token
    raise AssertionError("unreachable")


async def list_for(db: AsyncSession, user: User) -> list[ApiKey]:
    """This user's keys, newest first. Expired and disabled ones included."""
    rows = await db.scalars(
        select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())
    )
    return list(rows)


async def get_owned(db: AsyncSession, user: User, key_id: int) -> ApiKey | None:
    """One key, only if this user owns it.

    Ownership is part of the query, not a check after the fact: a route that
    loads first and compares second is one forgotten comparison away from
    letting anyone disable anyone's key.
    """
    return await db.scalar(select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user.id))


async def set_enabled(db: AsyncSession, key: ApiKey, enabled: bool) -> ApiKey:
    key.enabled = enabled
    await db.commit()
    await db.refresh(key)
    return key


async def delete(db: AsyncSession, key: ApiKey) -> None:
    await db.execute(sa_delete(ApiKey).where(ApiKey.id == key.id))
    await db.commit()


async def authenticate(db: AsyncSession, token: str) -> ApiKey:
    """Resolve a token to its key, or raise a :class:`KeyRejected`.

    The order matters. An unknown prefix and a wrong digest raise the *same*
    exception, so a guessed token cannot confirm that a real one exists. The two
    specific rejections come only after the digest matched — by then the caller
    demonstrably holds the key, and telling them why it stopped working is what
    saves them an afternoon.
    """
    row = await db.scalar(select(ApiKey).where(ApiKey.token_prefix == token[:PREFIX_LEN]))
    if row is None or not hmac.compare_digest(row.token_hash, hash_token(token)):
        raise UnknownKey
    if not row.enabled:
        raise KeyDisabled
    if row.expires_at is not None and _as_utc(row.expires_at) <= datetime.now(UTC):
        raise KeyExpired
    return row


async def touch(db: AsyncSession, key: ApiKey) -> None:
    """Record use, at most once per :data:`TOUCH_INTERVAL`.

    Its own committed statement: a read-only route never commits, so a write
    left pending in the request's session would simply be discarded.
    """
    now = datetime.now(UTC)
    if key.last_used_at is not None and now - _as_utc(key.last_used_at) < TOUCH_INTERVAL:
        return
    await db.execute(update(ApiKey).where(ApiKey.id == key.id).values(last_used_at=now))
    await db.commit()
    key.last_used_at = now


__all__ = [
    "ApiKeyLimitReached",
    "KeyDisabled",
    "KeyExpired",
    "KeyRejected",
    "MAX_KEYS",
    "PREFIX_LEN",
    "TOKEN_PREFIX",
    "TOUCH_INTERVAL",
    "UnknownKey",
    "authenticate",
    "create",
    "delete",
    "generate_token",
    "get_owned",
    "hash_token",
    "list_for",
    "set_enabled",
    "touch",
]
