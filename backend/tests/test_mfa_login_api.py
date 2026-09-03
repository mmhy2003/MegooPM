"""Login with a second factor. SQLite-backed; Redis is faked."""

from __future__ import annotations

import time

import pyotp
import pytest
from app.models.user import User
from app.services import rate_limit, totp
from httpx import AsyncClient

LOGIN = "/api/v1/auth/login"
VERIFY = "/api/v1/auth/mfa/verify"
REFRESH = "/api/v1/auth/refresh"
ME = "/api/v1/auth/me"


def _next_code(secret: str) -> str:
    """The code for the *next* step. Enabling records the current step, so
    re-using the enrolment code would be a replay — which is the point."""
    return pyotp.TOTP(secret).at(int(time.time()) + 30)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key: str, seconds: int) -> bool:
        return True

    async def ttl(self, key: str) -> int:
        return 300

    async def aclose(self) -> None:
        pass


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    client = FakeRedis()
    monkeypatch.setattr(rate_limit, "_client", lambda: client)
    return client


@pytest.fixture
async def enabled(session_factory, admin_user: User) -> tuple[str, list[str]]:
    """admin_user with 2FA on; returns (secret, recovery codes)."""
    async with session_factory() as db:
        user = await db.get(User, admin_user.id)
        raw = await totp.start_enrolment(db, user)
        codes = await totp.confirm_enrolment(db, user, pyotp.TOTP(raw).now())
        assert codes is not None
        return raw, codes


async def test_login_without_2fa_is_unchanged(db_client: AsyncClient, admin_user: User) -> None:
    resp = await db_client.post(LOGIN, json={"email": admin_user.email, "password": "adminpass123"})
    assert resp.status_code == 200
    assert "access_token" in resp.json()
    assert "mfa_required" not in resp.json()


async def test_login_with_2fa_returns_a_challenge_and_no_tokens(
    db_client: AsyncClient, admin_user: User, enabled
) -> None:
    resp = await db_client.post(LOGIN, json={"email": admin_user.email, "password": "adminpass123"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mfa_required"] is True
    assert body["mfa_token"]
    assert "access_token" not in body


async def test_a_wrong_password_is_still_401_with_2fa_on(
    db_client: AsyncClient, admin_user: User, enabled
) -> None:
    # The challenge must not leak that the password was right.
    resp = await db_client.post(LOGIN, json={"email": admin_user.email, "password": "wrong"})
    assert resp.status_code == 401


async def _challenge(db_client: AsyncClient, admin_user: User) -> str:
    resp = await db_client.post(LOGIN, json={"email": admin_user.email, "password": "adminpass123"})
    return resp.json()["mfa_token"]


async def test_verify_with_a_current_code_returns_tokens_that_work(
    db_client: AsyncClient, admin_user: User, enabled, fake_redis
) -> None:
    secret, _ = enabled
    mfa_token = await _challenge(db_client, admin_user)

    resp = await db_client.post(VERIFY, json={"mfa_token": mfa_token, "code": _next_code(secret)})

    assert resp.status_code == 200, resp.text
    tokens = resp.json()
    me = await db_client.get(ME, headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == admin_user.email


async def test_verify_with_a_recovery_code_works_and_reports_the_remainder(
    db_client: AsyncClient, admin_user: User, enabled, fake_redis
) -> None:
    _, codes = enabled
    mfa_token = await _challenge(db_client, admin_user)

    resp = await db_client.post(VERIFY, json={"mfa_token": mfa_token, "code": codes[3]})

    assert resp.status_code == 200, resp.text
    assert resp.json()["recovery_codes_remaining"] == 9


async def test_a_totp_verify_reports_no_remainder(
    db_client: AsyncClient, admin_user: User, enabled, fake_redis
) -> None:
    secret, _ = enabled
    mfa_token = await _challenge(db_client, admin_user)
    resp = await db_client.post(VERIFY, json={"mfa_token": mfa_token, "code": _next_code(secret)})
    assert resp.json()["recovery_codes_remaining"] is None


async def test_verify_with_a_wrong_code_is_401(
    db_client: AsyncClient, admin_user: User, enabled, fake_redis
) -> None:
    mfa_token = await _challenge(db_client, admin_user)
    resp = await db_client.post(VERIFY, json={"mfa_token": mfa_token, "code": "000000"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == totp.INVALID_CODE_MESSAGE


async def test_a_bad_mfa_token_gets_the_same_message_as_a_wrong_code(
    db_client: AsyncClient, admin_user: User, enabled, fake_redis
) -> None:
    # Telling an attacker the token expired is telling them the password
    # was right.
    wrong_code = await db_client.post(
        VERIFY, json={"mfa_token": await _challenge(db_client, admin_user), "code": "000000"}
    )
    bad_token = await db_client.post(VERIFY, json={"mfa_token": "nope", "code": "000000"})
    assert wrong_code.status_code == bad_token.status_code == 401
    assert wrong_code.json() == bad_token.json()


async def test_the_eleventh_attempt_is_429(
    db_client: AsyncClient, admin_user: User, enabled, fake_redis
) -> None:
    mfa_token = await _challenge(db_client, admin_user)
    for _ in range(rate_limit.MFA_ATTEMPT_LIMIT):
        resp = await db_client.post(VERIFY, json={"mfa_token": mfa_token, "code": "000000"})
        assert resp.status_code == 401
    resp = await db_client.post(VERIFY, json={"mfa_token": mfa_token, "code": "000000"})
    assert resp.status_code == 429


async def test_a_version_bump_between_login_and_verify_kills_the_challenge(
    db_client: AsyncClient, admin_user: User, enabled, fake_redis, session_factory
) -> None:
    secret, _ = enabled
    mfa_token = await _challenge(db_client, admin_user)

    from app.services import user as user_service

    async with session_factory() as db:
        user = await db.get(User, admin_user.id)
        await user_service.set_password(db, user, "changed12345")

    resp = await db_client.post(VERIFY, json={"mfa_token": mfa_token, "code": _next_code(secret)})
    assert resp.status_code == 401


async def test_an_access_token_cannot_be_used_as_a_challenge(
    db_client: AsyncClient, admin_user: User, enabled, fake_redis
) -> None:
    from app.core.security import create_access_token

    forged = create_access_token(admin_user.id, "admin", token_version=admin_user.token_version + 1)
    resp = await db_client.post(VERIFY, json={"mfa_token": forged, "code": "000000"})
    assert resp.status_code == 401


async def test_a_pending_enrolment_does_not_challenge(
    db_client: AsyncClient, admin_user: User, session_factory
) -> None:
    async with session_factory() as db:
        user = await db.get(User, admin_user.id)
        await totp.start_enrolment(db, user)

    resp = await db_client.post(LOGIN, json={"email": admin_user.email, "password": "adminpass123"})
    assert "access_token" in resp.json()
