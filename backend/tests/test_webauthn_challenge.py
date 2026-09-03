"""The challenge store: one nonce, one read."""

from __future__ import annotations

import pytest
from app.services.webauthn_challenge import (
    ChallengeStoreUnavailable,
    put,
    take,
)
from redis.exceptions import ConnectionError as RedisConnectionError


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key: str, value: str, *, ex: int | None = None) -> bool:
        self.values[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def getdel(self, key: str) -> str | None:
        return self.values.pop(key, None)

    async def aclose(self) -> None:
        pass


class DownRedis(FakeRedis):
    async def set(self, key: str, value: str, *, ex: int | None = None) -> bool:
        raise RedisConnectionError("down")

    async def getdel(self, key: str) -> str | None:
        raise RedisConnectionError("down")


async def test_put_then_take_returns_the_user_and_the_challenge() -> None:
    client = FakeRedis()
    nonce = await put(kind="register", user_id=7, challenge=b"\x01\x02", client=client)
    assert await take(kind="register", nonce=nonce, client=client) == (7, b"\x01\x02")


async def test_a_challenge_is_spent_on_the_first_take() -> None:
    # A captured assertion resubmitted a second later must find nothing.
    client = FakeRedis()
    nonce = await put(kind="authenticate", user_id=7, challenge=b"c", client=client)
    assert await take(kind="authenticate", nonce=nonce, client=client) is not None
    assert await take(kind="authenticate", nonce=nonce, client=client) is None


async def test_the_kind_is_part_of_the_key() -> None:
    # A registration challenge must never be presented as an authentication one.
    client = FakeRedis()
    nonce = await put(kind="register", user_id=7, challenge=b"c", client=client)
    assert await take(kind="authenticate", nonce=nonce, client=client) is None


async def test_challenges_expire() -> None:
    client = FakeRedis()
    nonce = await put(kind="register", user_id=7, challenge=b"c", client=client)
    assert client.ttls[f"megoopm:webauthn:register:{nonce}"] == 300


async def test_nonces_are_unguessable_and_distinct() -> None:
    client = FakeRedis()
    a = await put(kind="register", user_id=7, challenge=b"c", client=client)
    b = await put(kind="register", user_id=7, challenge=b"c", client=client)
    assert a != b and len(a) >= 40


async def test_an_unknown_nonce_is_none() -> None:
    assert await take(kind="register", nonce="nope", client=FakeRedis()) is None


async def test_redis_down_raises_the_typed_error() -> None:
    with pytest.raises(ChallengeStoreUnavailable):
        await put(kind="register", user_id=7, challenge=b"c", client=DownRedis())
    with pytest.raises(ChallengeStoreUnavailable):
        await take(kind="register", nonce="x", client=DownRedis())
