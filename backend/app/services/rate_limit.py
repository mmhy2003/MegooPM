"""Fixed-window counters in Redis, for the unauthenticated email-sending routes.

An endpoint that sends email with no limit is an outbound spam cannon: request
resets for a thousand addresses and the mail server delivers to all of them,
which is how a sending domain ends up on a blocklist.

Fails closed. This is a security control on a security appliance, and a Redis
outage already takes Celery with it — degrading silently is the wrong default.
"""

from __future__ import annotations

import hashlib

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.core.config import settings

RESET_EMAIL_LIMIT = 3
RESET_IP_LIMIT = 10
RESET_WINDOW_S = 3600

MFA_ATTEMPT_LIMIT = 10
MFA_WINDOW_S = 300

_PREFIX = "megoopm:ratelimit"


class RateLimited(Exception):
    """Over the limit. ``retry_after`` is seconds until the window resets."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("Too many requests")
        self.retry_after = max(1, retry_after)


class RateLimitUnavailable(Exception):
    """Redis could not be consulted. The caller fails closed."""


def _client() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def hit(client: aioredis.Redis, key: str, *, limit: int, window_s: int) -> None:
    """Count one hit on ``key``; raise when it exceeds ``limit`` in ``window_s``."""
    try:
        count = await client.incr(key)
        if count == 1:
            # Only on the first hit. Re-arming EXPIRE on every hit would keep
            # a busy key alive forever, turning a window into a permanent ban.
            await client.expire(key, window_s)
        if count > limit:
            ttl = await client.ttl(key)
            raise RateLimited(retry_after=ttl if ttl > 0 else window_s)
    except (RedisError, OSError) as exc:
        raise RateLimitUnavailable(str(exc)) from exc


async def check_password_reset(
    *, email: str, ip: str, client: aioredis.Redis | None = None
) -> None:
    """Both password-reset limits. Raises :class:`RateLimited` or
    :class:`RateLimitUnavailable`; returns silently when allowed."""
    # Hashed: Redis keys are visible to anyone with Redis access, and an
    # address is personal data. Lower-cased first, as login is.
    email_key = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()
    own = client is None
    redis_client = client if client is not None else _client()
    try:
        await hit(
            redis_client,
            f"{_PREFIX}:reset:email:{email_key}",
            limit=RESET_EMAIL_LIMIT,
            window_s=RESET_WINDOW_S,
        )
        await hit(
            redis_client,
            f"{_PREFIX}:reset:ip:{ip}",
            limit=RESET_IP_LIMIT,
            window_s=RESET_WINDOW_S,
        )
    finally:
        if own:
            await redis_client.aclose()


async def check_password_reset_redeem(*, ip: str, client: aioredis.Redis | None = None) -> None:
    """The per-IP limit for spending a token. No address to key on here; the
    point is that a token cannot be brute-forced from one client."""
    own = client is None
    redis_client = client if client is not None else _client()
    try:
        await hit(
            redis_client,
            f"{_PREFIX}:reset-redeem:ip:{ip}",
            limit=RESET_IP_LIMIT,
            window_s=RESET_WINDOW_S,
        )
    finally:
        if own:
            await redis_client.aclose()


async def check_mfa_verify(*, user_id: int, ip: str, client: aioredis.Redis | None = None) -> None:
    """Both limits on the code-entry step: per user (from the mfa token's
    subject) and per IP."""
    own = client is None
    redis_client = client if client is not None else _client()
    try:
        await hit(
            redis_client,
            f"{_PREFIX}:mfa:user:{user_id}",
            limit=MFA_ATTEMPT_LIMIT,
            window_s=MFA_WINDOW_S,
        )
        await hit(
            redis_client,
            f"{_PREFIX}:mfa:ip:{ip}",
            limit=RESET_IP_LIMIT,
            window_s=RESET_WINDOW_S,
        )
    finally:
        if own:
            await redis_client.aclose()


__all__ = [
    "MFA_ATTEMPT_LIMIT",
    "MFA_WINDOW_S",
    "RESET_EMAIL_LIMIT",
    "RESET_IP_LIMIT",
    "RESET_WINDOW_S",
    "RateLimitUnavailable",
    "RateLimited",
    "check_password_reset",
    "check_mfa_verify",
    "check_password_reset_redeem",
    "hit",
]
