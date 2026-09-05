"""The token service, against the SQLite session factory. No routes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.models.auth_token import AuthToken
from app.models.enums import AuthTokenKind
from app.models.user import User
from app.services.auth_tokens import RESET_TTL, TokenInvalid, hash_token, issue, redeem
from sqlalchemy import select

KIND = AuthTokenKind.password_reset


async def test_issue_returns_a_token_and_stores_only_its_hash(
    session_factory, admin_user: User
) -> None:
    # A database leak must not hand over live reset links, for the same reason
    # it must not hand over passwords.
    async with session_factory() as db:
        raw = await issue(db, user=admin_user, kind=KIND, ttl=RESET_TTL)
        rows = (await db.execute(select(AuthToken))).scalars().all()

    assert len(raw) >= 40
    assert len(rows) == 1
    assert rows[0].token_hash == hash_token(raw)
    assert raw not in rows[0].token_hash


async def test_redeem_returns_the_row_for_the_right_user(session_factory, admin_user: User) -> None:
    async with session_factory() as db:
        raw = await issue(db, user=admin_user, kind=KIND, ttl=RESET_TTL)
        row = await redeem(db, raw=raw, kind=KIND)

    assert row.user_id == admin_user.id
    assert row.used_at is not None


async def test_a_token_cannot_be_redeemed_twice(session_factory, admin_user: User) -> None:
    async with session_factory() as db:
        raw = await issue(db, user=admin_user, kind=KIND, ttl=RESET_TTL)
        await redeem(db, raw=raw, kind=KIND)
        with pytest.raises(TokenInvalid):
            await redeem(db, raw=raw, kind=KIND)


async def test_an_expired_token_is_refused(session_factory, admin_user: User) -> None:
    async with session_factory() as db:
        raw = await issue(db, user=admin_user, kind=KIND, ttl=timedelta(seconds=-1))
        with pytest.raises(TokenInvalid):
            await redeem(db, raw=raw, kind=KIND)


async def test_an_unknown_token_is_refused(session_factory) -> None:
    async with session_factory() as db:
        with pytest.raises(TokenInvalid):
            await redeem(db, raw="not-a-real-token", kind=KIND)


async def test_a_tampered_token_is_refused(session_factory, admin_user: User) -> None:
    async with session_factory() as db:
        raw = await issue(db, user=admin_user, kind=KIND, ttl=RESET_TTL)
        tampered = raw[:-1] + ("A" if raw[-1] != "A" else "B")
        with pytest.raises(TokenInvalid):
            await redeem(db, raw=tampered, kind=KIND)


async def test_issuing_again_kills_the_earlier_token(session_factory, admin_user: User) -> None:
    # Two live links for one account is one more than anyone needs, and it is
    # the state an attacker requesting resets in parallel would try to create.
    async with session_factory() as db:
        first = await issue(db, user=admin_user, kind=KIND, ttl=RESET_TTL)
        second = await issue(db, user=admin_user, kind=KIND, ttl=RESET_TTL)

        with pytest.raises(TokenInvalid):
            await redeem(db, raw=first, kind=KIND)
        row = await redeem(db, raw=second, kind=KIND)
        assert row.user_id == admin_user.id


async def test_every_refusal_is_the_same_exception(session_factory, admin_user: User) -> None:
    # Distinguishing absent / expired / used tells an attacker which guesses
    # were once valid. One exception type, one message.
    async with session_factory() as db:
        expired = await issue(db, user=admin_user, kind=KIND, ttl=timedelta(seconds=-1))
        used = await issue(db, user=admin_user, kind=KIND, ttl=RESET_TTL)
        await redeem(db, raw=used, kind=KIND)

        messages = set()
        for raw in ("absent", expired, used):
            with pytest.raises(TokenInvalid) as info:
                await redeem(db, raw=raw, kind=KIND)
            messages.add(str(info.value))
        assert len(messages) == 1


def test_hash_token_is_stable_and_hex() -> None:
    assert hash_token("abc") == hash_token("abc")
    assert len(hash_token("abc")) == 64
    int(hash_token("abc"), 16)


async def test_expiry_is_roughly_the_ttl_out(session_factory, admin_user: User) -> None:
    async with session_factory() as db:
        await issue(db, user=admin_user, kind=KIND, ttl=RESET_TTL)
        row = (await db.execute(select(AuthToken))).scalar_one()
    # SQLite returns naive; the service must compare in UTC either way. This
    # pins that expires_at is roughly one hour out, whatever the driver does.
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
    delta = expires - datetime.now(UTC)
    assert timedelta(minutes=55) < delta <= timedelta(hours=1)
