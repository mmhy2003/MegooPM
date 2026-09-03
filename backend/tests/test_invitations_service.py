"""Inviting and accepting, against the SQLite session factory. No routes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.core.security import verify_password
from app.models.enums import AuthTokenKind
from app.models.user import User, UserRole
from app.services import user as user_service
from app.services.auth_tokens import INVITE_TTL, TokenInvalid, issue, redeem


async def test_an_invited_user_is_a_real_but_inactive_row(session_factory) -> None:
    async with session_factory() as db:
        user = await user_service.invite_user(
            db, email="New@Example.com", full_name="", role=UserRole.member
        )

    assert user.email == "new@example.com"
    assert user.is_active is False
    assert user.invited_at is not None
    assert user.role is UserRole.member


async def test_the_placeholder_password_verifies_against_nothing_obvious(
    session_factory,
) -> None:
    # A real Argon2 hash of random bytes: the login path verifies it like any
    # other, so there is no timing shortcut that says "no real password here".
    async with session_factory() as db:
        user = await user_service.invite_user(
            db, email="new@example.com", full_name="", role=UserRole.member
        )

    assert user.hashed_password.startswith("$argon2")
    for guess in ("", "password", "new@example.com", user.hashed_password):
        assert verify_password(guess, user.hashed_password) is False


async def test_two_invites_get_different_placeholders(session_factory) -> None:
    async with session_factory() as db:
        a = await user_service.invite_user(
            db, email="a@example.com", full_name="", role=UserRole.member
        )
        b = await user_service.invite_user(
            db, email="b@example.com", full_name="", role=UserRole.member
        )
    assert a.hashed_password != b.hashed_password


async def test_a_taken_address_is_refused(session_factory, admin_user: User) -> None:
    async with session_factory() as db:
        with pytest.raises(user_service.EmailAlreadyExistsError):
            await user_service.invite_user(
                db, email=admin_user.email.upper(), full_name="", role=UserRole.member
            )


async def test_an_already_invited_address_is_refused(session_factory) -> None:
    async with session_factory() as db:
        await user_service.invite_user(
            db, email="new@example.com", full_name="", role=UserRole.member
        )
        with pytest.raises(user_service.EmailAlreadyExistsError):
            await user_service.invite_user(
                db, email="new@example.com", full_name="", role=UserRole.member
            )


async def test_accepting_sets_everything_and_activates(session_factory) -> None:
    async with session_factory() as db:
        user = await user_service.invite_user(
            db, email="new@example.com", full_name="", role=UserRole.member
        )
        await user_service.accept_invitation(
            db, user, full_name="New Person", password="chosen12345"
        )

    assert user.invited_at is None
    assert user.is_active is True
    assert user.full_name == "New Person"
    assert verify_password("chosen12345", user.hashed_password) is True


async def test_accepting_bumps_the_token_version(session_factory) -> None:
    # No sessions exist yet, but the invariant "a password was set, therefore
    # the version moved" should hold everywhere it is set.
    async with session_factory() as db:
        user = await user_service.invite_user(
            db, email="new@example.com", full_name="", role=UserRole.member
        )
        before = user.token_version
        await user_service.accept_invitation(db, user, full_name="N", password="chosen12345")
    assert user.token_version == before + 1


async def test_invitation_tokens_live_seven_days(session_factory) -> None:
    assert INVITE_TTL == timedelta(days=7)
    async with session_factory() as db:
        user = await user_service.invite_user(
            db, email="new@example.com", full_name="", role=UserRole.member
        )
        raw = await issue(db, user=user, kind=AuthTokenKind.invitation, ttl=INVITE_TTL)
        row = await redeem(db, raw=raw, kind=AuthTokenKind.invitation)
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
    assert timedelta(days=6, hours=23) < expires - datetime.now(UTC) <= timedelta(days=7)


async def test_a_reset_token_cannot_accept_an_invitation(session_factory) -> None:
    # The kind is part of the contract: a password-reset link for an invited
    # user must not double as their invitation.
    async with session_factory() as db:
        user = await user_service.invite_user(
            db, email="new@example.com", full_name="", role=UserRole.member
        )
        raw = await issue(db, user=user, kind=AuthTokenKind.password_reset, ttl=INVITE_TTL)
        with pytest.raises(TokenInvalid):
            await redeem(db, raw=raw, kind=AuthTokenKind.invitation)


async def test_a_fresh_invitation_supersedes_the_old_token(session_factory) -> None:
    async with session_factory() as db:
        user = await user_service.invite_user(
            db, email="new@example.com", full_name="", role=UserRole.member
        )
        first = await issue(db, user=user, kind=AuthTokenKind.invitation, ttl=INVITE_TTL)
        second = await issue(db, user=user, kind=AuthTokenKind.invitation, ttl=INVITE_TTL)
        with pytest.raises(TokenInvalid):
            await redeem(db, raw=first, kind=AuthTokenKind.invitation)
        assert (await redeem(db, raw=second, kind=AuthTokenKind.invitation)).user_id == user.id
