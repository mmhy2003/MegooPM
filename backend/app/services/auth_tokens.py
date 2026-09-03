"""Issue and redeem single-use tokens.

The raw token leaves this module exactly once — as the return value of
:func:`issue`, on its way into an email — and is never stored or logged.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth_token import AuthToken
from app.models.enums import AuthTokenKind
from app.models.user import User

#: Long enough for a slow mail server and a distracted user; short enough that
#: a link found in a mailbox next week is dead.
RESET_TTL = timedelta(hours=1)

#: A reset is a same-hour action by someone at the keyboard; an invitation is
#: opened when the invitee gets to it, which is next week as often as not.
INVITE_TTL = timedelta(days=7)


class TokenInvalid(Exception):
    """The token is absent, expired, spent, or of the wrong kind.

    One exception and one message for all four: distinguishing them tells an
    attacker which of their guesses were once valid.
    """

    def __init__(self) -> None:
        super().__init__("This link is invalid or has expired.")


def hash_token(raw: str) -> str:
    """SHA-256 hex of the raw token.

    Not Argon2: the token carries 256 bits of entropy, and a slow hash exists
    to protect low-entropy secrets. Here it would only slow the lookup.
    """
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


async def issue(db: AsyncSession, *, user: User, kind: AuthTokenKind, ttl: timedelta) -> str:
    """Mint a token for ``user``, superseding any outstanding one of ``kind``.

    Returns the raw token. Commits.
    """
    now = _now()
    # Supersede first: every unspent token of this kind for this user is
    # marked used. Two live links for one account is the state an attacker
    # requesting resets in parallel would try to create.
    await db.execute(
        update(AuthToken)
        .where(
            AuthToken.user_id == user.id,
            AuthToken.kind == kind,
            AuthToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    raw = secrets.token_urlsafe(32)
    db.add(
        AuthToken(
            kind=kind,
            token_hash=hash_token(raw),
            user_id=user.id,
            expires_at=now + ttl,
        )
    )
    await db.commit()
    return raw


async def redeem(db: AsyncSession, *, raw: str, kind: AuthTokenKind) -> AuthToken:
    """Spend a token and return its row, or raise :class:`TokenInvalid`. Commits."""
    row = (
        await db.execute(select(AuthToken).where(AuthToken.token_hash == hash_token(raw)))
    ).scalar_one_or_none()
    if row is None or row.kind != kind or row.used_at is not None:
        raise TokenInvalid()
    # SQLite hands back naive datetimes; Postgres hands back aware ones. Compare
    # in UTC either way rather than trusting the driver.
    expires_at = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
    if expires_at <= _now():
        raise TokenInvalid()
    row.used_at = _now()
    await db.commit()
    return row


__all__ = ["INVITE_TTL", "RESET_TTL", "TokenInvalid", "hash_token", "issue", "redeem"]
