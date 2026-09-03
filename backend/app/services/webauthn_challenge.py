"""Server-side WebAuthn challenges: issued for one ceremony, read once.

A challenge must be unpredictable, bound to the user who asked for it, and
unusable twice. Redis gives the last property for free with GETDEL, which is
why the challenge is not carried in a signed token: a token can be replayed
until it expires, and on the authentication path a replayed assertion is a
second session for an attacker.
"""

from __future__ import annotations

import secrets
from typing import Literal

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.core.redis import redis_client

ChallengeKind = Literal["register", "authenticate"]

_PREFIX = "megoopm:webauthn"
_TTL_S = 300


class ChallengeStoreUnavailable(Exception):
    """Redis could not be consulted. The caller answers 503."""


def _key(kind: ChallengeKind, nonce: str) -> str:
    return f"{_PREFIX}:{kind}:{nonce}"


async def put(
    *, kind: ChallengeKind, user_id: int, challenge: bytes, client: aioredis.Redis | None = None
) -> str:
    """Store ``challenge`` for ``user_id`` under a fresh nonce; return the nonce."""
    own = client is None
    redis = client if client is not None else redis_client()
    nonce = secrets.token_urlsafe(32)
    try:
        await redis.set(_key(kind, nonce), f"{user_id}:{challenge.hex()}", ex=_TTL_S)
    except RedisError as exc:
        raise ChallengeStoreUnavailable() from exc
    finally:
        if own:
            await redis.aclose()
    return nonce


async def take(
    *, kind: ChallengeKind, nonce: str, client: aioredis.Redis | None = None
) -> tuple[int, bytes] | None:
    """Read and delete in one step. ``None`` when missing, expired, or spent."""
    own = client is None
    redis = client if client is not None else redis_client()
    try:
        raw = await redis.getdel(_key(kind, nonce))
    except RedisError as exc:
        raise ChallengeStoreUnavailable() from exc
    finally:
        if own:
            await redis.aclose()
    if not raw:
        return None
    user_id, _, challenge_hex = raw.partition(":")
    return int(user_id), bytes.fromhex(challenge_hex)


__all__ = ["ChallengeKind", "ChallengeStoreUnavailable", "put", "take"]
