"""Generating, verifying and expiring a key. No HTTP here."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.models.user import User
from app.services import api_keys
from app.services import user as user_service


def test_a_token_is_prefixed_and_long_enough() -> None:
    token, prefix, digest = api_keys.generate_token()

    assert token.startswith("mgm_")
    # 32 bytes of urlsafe base64. Anything shorter is a weaker secret than the
    # design claims, and that claim is what justifies hashing it with SHA-256.
    assert len(token) >= 43
    assert prefix == token[:12]
    assert digest == api_keys.hash_token(token)


def test_two_tokens_never_match() -> None:
    first, _, _ = api_keys.generate_token()
    second, _, _ = api_keys.generate_token()

    assert first != second


def test_the_digest_is_not_the_token() -> None:
    # The whole point of storing a digest: the table cannot be replayed.
    token, _, digest = api_keys.generate_token()

    assert digest != token
    assert token not in digest
    assert len(digest) == 64


async def _user(db, email: str = "keys@example.com") -> User:
    return await user_service.create_user(
        db, email=email, password="password123", full_name="K"
    )


async def test_a_minted_key_authenticates(session_factory) -> None:
    async with session_factory() as db:
        user = await _user(db)
        _, token = await api_keys.create(db, user, name="CI", expires_at=None)

        resolved = await api_keys.authenticate(db, token)

    assert resolved.user_id == user.id


async def test_a_wrong_token_is_indistinguishable_from_an_unknown_one(session_factory) -> None:
    # Both raise UnknownKey: a guess must not confirm that a real key exists.
    async with session_factory() as db:
        user = await _user(db)
        _, token = await api_keys.create(db, user, name="CI", expires_at=None)
        wrong = token[:-1] + ("A" if token[-1] != "A" else "B")

        with pytest.raises(api_keys.UnknownKey):
            await api_keys.authenticate(db, wrong)
        with pytest.raises(api_keys.UnknownKey):
            await api_keys.authenticate(db, "mgm_nothing_like_it")


async def test_a_disabled_key_is_rejected_as_disabled(session_factory) -> None:
    async with session_factory() as db:
        user = await _user(db)
        row, token = await api_keys.create(db, user, name="CI", expires_at=None)
        await api_keys.set_enabled(db, row, False)

        with pytest.raises(api_keys.KeyDisabled):
            await api_keys.authenticate(db, token)


async def test_an_expired_key_is_rejected_as_expired(session_factory) -> None:
    async with session_factory() as db:
        user = await _user(db)
        _, token = await api_keys.create(
            db, user, name="CI", expires_at=datetime.now(UTC) - timedelta(seconds=1)
        )

        with pytest.raises(api_keys.KeyExpired):
            await api_keys.authenticate(db, token)


async def test_no_expiry_means_no_expiry(session_factory) -> None:
    async with session_factory() as db:
        user = await _user(db)
        _, token = await api_keys.create(db, user, name="forever", expires_at=None)

        assert (await api_keys.authenticate(db, token)).name == "forever"


async def test_the_twenty_first_key_is_refused(session_factory) -> None:
    # A bound, so one minute of a stolen session cannot leave an unbounded pile
    # of persistent credentials behind.
    async with session_factory() as db:
        user = await _user(db)
        for i in range(api_keys.MAX_KEYS):
            await api_keys.create(db, user, name=f"k{i}", expires_at=None)

        with pytest.raises(api_keys.ApiKeyLimitReached):
            await api_keys.create(db, user, name="one too many", expires_at=None)


async def test_a_second_use_inside_the_window_writes_nothing(session_factory) -> None:
    async with session_factory() as db:
        user = await _user(db)
        row, _ = await api_keys.create(db, user, name="CI", expires_at=None)

        await api_keys.touch(db, row)
        first = row.last_used_at
        await api_keys.touch(db, row)

        assert first is not None
        assert row.last_used_at == first


async def test_a_prefix_collision_is_retried(session_factory, monkeypatch) -> None:
    # Forced, because 72 bits will not collide on its own: the retry exists so
    # that "cannot happen" does not become a 500 once every few years.
    async with session_factory() as db:
        user = await _user(db)
        first, _ = await api_keys.create(db, user, name="first", expires_at=None)
        taken = first.token_prefix

        real = api_keys.generate_token
        calls = {"n": 0}

        def colliding_once() -> tuple[str, str, str]:
            calls["n"] += 1
            token, prefix, digest = real()
            return (token, taken, digest) if calls["n"] == 1 else (token, prefix, digest)

        monkeypatch.setattr(api_keys, "generate_token", colliding_once)
        row, _ = await api_keys.create(db, user, name="second", expires_at=None)

    assert calls["n"] == 2
    assert row.token_prefix != taken


async def test_another_users_key_is_not_owned(session_factory) -> None:
    async with session_factory() as db:
        owner = await _user(db)
        other = await _user(db, "other@example.com")
        row, _ = await api_keys.create(db, owner, name="CI", expires_at=None)

        assert await api_keys.get_owned(db, other, row.id) is None
        assert await api_keys.get_owned(db, owner, row.id) is not None
