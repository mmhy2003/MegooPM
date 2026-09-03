"""One place to open a Redis connection for request-scoped work.

Celery has its own; this is for the small, synchronous-feeling things —
rate-limit counters, WebAuthn challenges — that a request opens and closes.
"""

from __future__ import annotations

import redis.asyncio as aioredis

from app.core.config import settings


def redis_client() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


__all__ = ["redis_client"]
