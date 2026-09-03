"""The limiter against an in-memory fake. No Redis needed."""

from __future__ import annotations

import pytest
from app.services.rate_limit import (
    RESET_EMAIL_LIMIT,
    RESET_IP_LIMIT,
    RateLimited,
    RateLimitUnavailable,
    check_password_reset,
    check_password_reset_redeem,
    hit,
)
from redis.exceptions import ConnectionError as RedisConnectionError


class FakeRedis:
    """The three commands the limiter uses, over a dict."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.ttls[key] = seconds
        return True

    async def ttl(self, key: str) -> int:
        return self.ttls.get(key, -1)

    async def aclose(self) -> None:
        pass


class DeadRedis(FakeRedis):
    async def incr(self, key: str) -> int:
        raise RedisConnectionError("Connection refused")


async def test_the_first_hits_pass() -> None:
    client = FakeRedis()
    for _ in range(3):
        await hit(client, "k", limit=3, window_s=60)


async def test_the_hit_over_the_limit_is_refused_with_a_retry_after() -> None:
    client = FakeRedis()
    for _ in range(3):
        await hit(client, "k", limit=3, window_s=60)
    with pytest.raises(RateLimited) as info:
        await hit(client, "k", limit=3, window_s=60)
    assert info.value.retry_after == 60


async def test_the_window_is_set_on_the_first_hit_only() -> None:
    # Re-setting EXPIRE on every hit would keep a busy key alive forever.
    client = FakeRedis()
    await hit(client, "k", limit=3, window_s=60)
    client.ttls["k"] = 10  # pretend fifty seconds passed
    await hit(client, "k", limit=3, window_s=60)
    assert client.ttls["k"] == 10


async def test_redis_down_fails_closed() -> None:
    # A security control on a security appliance; "allow everything" is the
    # wrong default when the control cannot be consulted.
    with pytest.raises(RateLimitUnavailable):
        await hit(DeadRedis(), "k", limit=3, window_s=60)


async def test_password_reset_limits_the_address() -> None:
    client = FakeRedis()
    for _ in range(RESET_EMAIL_LIMIT):
        await check_password_reset(email="a@example.com", ip="1.1.1.1", client=client)
    with pytest.raises(RateLimited):
        await check_password_reset(email="a@example.com", ip="1.1.1.1", client=client)


async def test_password_reset_limits_the_ip_across_addresses() -> None:
    # One client cycling through addresses is what the per-IP limit stops.
    client = FakeRedis()
    for i in range(RESET_IP_LIMIT):
        await check_password_reset(email=f"u{i}@example.com", ip="1.1.1.1", client=client)
    with pytest.raises(RateLimited):
        await check_password_reset(email="new@example.com", ip="1.1.1.1", client=client)


async def test_the_address_key_is_case_insensitive() -> None:
    # Login is case-insensitive on email; the limit must be too, or
    # A@x.com and a@x.com are six requests instead of three.
    client = FakeRedis()
    for _ in range(RESET_EMAIL_LIMIT):
        await check_password_reset(email="A@Example.com", ip="1.1.1.1", client=client)
    with pytest.raises(RateLimited):
        await check_password_reset(email="a@example.com", ip="1.1.1.1", client=client)


async def test_redeem_is_limited_per_ip() -> None:
    # The reset-password route has a token, not an address. Its limit exists
    # so a token cannot be brute-forced from one client.
    client = FakeRedis()
    for _ in range(RESET_IP_LIMIT):
        await check_password_reset_redeem(ip="1.1.1.1", client=client)
    with pytest.raises(RateLimited):
        await check_password_reset_redeem(ip="1.1.1.1", client=client)


async def test_the_address_is_not_stored_in_the_key() -> None:
    # Redis keys are visible to anyone with Redis access; the address is hashed.
    client = FakeRedis()
    await check_password_reset(email="secret@example.com", ip="1.1.1.1", client=client)
    assert not any("secret@example.com" in key for key in client.values)
