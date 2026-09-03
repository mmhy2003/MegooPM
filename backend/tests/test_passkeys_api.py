"""Register, list, remove; then sign in with one. Real signatures throughout."""

from __future__ import annotations

import pyotp
import pytest
from app.models.user import User
from app.services import instance_settings as settings_service
from app.services import passkeys, rate_limit, totp, webauthn_challenge
from httpx import AsyncClient

from tests.webauthn_fake import FakeAuthenticator

APP_URL = "http://localhost:3000"
RP_ID, ORIGIN = "localhost", "http://localhost:3000"
OPTIONS = "/api/v1/users/me/passkeys/options"
PASSKEYS = "/api/v1/users/me/passkeys"
LOGIN = "/api/v1/auth/login"
MFA_OPTIONS = "/api/v1/auth/mfa/passkey/options"
MFA_VERIFY = "/api/v1/auth/mfa/passkey/verify"
ME = "/api/v1/auth/me"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class FakeRedis:
    """Enough of Redis for the limiter and the challenge store."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.counts: dict[str, int] = {}

    async def set(self, key: str, value: str, *, ex: int | None = None) -> bool:
        self.values[key] = value
        return True

    async def getdel(self, key: str) -> str | None:
        return self.values.pop(key, None)

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

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
    monkeypatch.setattr(webauthn_challenge, "redis_client", lambda: client)
    return client


@pytest.fixture
async def app_url(session_factory) -> None:
    async with session_factory() as db:
        row = await settings_service.get_instance_settings(db)
        row.app_url = APP_URL
        await db.commit()


@pytest.fixture
async def totp_on(session_factory, member_user: User) -> list[str]:
    """member_user with 2FA on; returns their recovery codes.

    Tests spend recovery codes rather than TOTP codes: each is unique, so a
    test that needs three valid codes in a row is not fighting the replay
    guard or the thirty-second step.
    """
    async with session_factory() as db:
        user = await db.get(User, member_user.id)
        raw = await totp.start_enrolment(db, user)
        codes = await totp.confirm_enrolment(db, user, pyotp.TOTP(raw).now())
        assert codes is not None
        return codes


def _code(codes: list[str]) -> str:
    """A fresh, valid, never-used code."""
    return codes.pop()


async def _register(
    db_client: AsyncClient, token: str, codes: list[str], *, name: str = "MacBook"
) -> tuple[FakeAuthenticator, dict]:
    opts = await db_client.post(OPTIONS, headers=_auth(token), json={"code": _code(codes)})
    assert opts.status_code == 200, opts.text
    auth = FakeAuthenticator(RP_ID, ORIGIN)
    created = await db_client.post(
        PASSKEYS,
        headers=_auth(token),
        json={
            "nonce": opts.json()["nonce"],
            "name": name,
            "credential": auth.register(opts.json()["options"]),
        },
    )
    assert created.status_code == 201, created.text
    return auth, created.json()


# --- gates ---------------------------------------------------------------------


async def test_options_need_2fa(
    db_client: AsyncClient, member_token: str, app_url, fake_redis
) -> None:
    resp = await db_client.post(OPTIONS, headers=_auth(member_token), json={"code": "000000"})
    assert resp.status_code == 409
    assert "not on" in resp.json()["detail"]


async def test_options_need_the_app_url(
    db_client: AsyncClient, member_token: str, totp_on: list[str], fake_redis
) -> None:
    resp = await db_client.post(OPTIONS, headers=_auth(member_token), json={"code": _code(totp_on)})
    assert resp.status_code == 409
    assert "app URL" in resp.json()["detail"]


async def test_options_need_a_valid_code(
    db_client: AsyncClient, member_token: str, totp_on: list[str], app_url, fake_redis
) -> None:
    resp = await db_client.post(OPTIONS, headers=_auth(member_token), json={"code": "000000"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == totp.INVALID_CODE_MESSAGE


async def test_capabilities_report_passkeys_only_with_an_app_url(
    db_client: AsyncClient, session_factory
) -> None:
    assert (await db_client.get("/api/v1/auth/capabilities")).json()["passkeys"] is False
    async with session_factory() as db:
        row = await settings_service.get_instance_settings(db)
        row.app_url = APP_URL
        await db.commit()
    assert (await db_client.get("/api/v1/auth/capabilities")).json()["passkeys"] is True


# --- register ----------------------------------------------------------------------


async def test_register_stores_and_lists_without_the_key(
    db_client: AsyncClient, member_token: str, totp_on: list[str], app_url, fake_redis
) -> None:
    auth, created = await _register(db_client, member_token, totp_on)
    assert created["name"] == "MacBook"
    assert created["last_used_at"] is None
    assert set(created) == {"id", "name", "created_at", "last_used_at"}

    listed = await db_client.get(PASSKEYS, headers=_auth(member_token))
    assert [p["id"] for p in listed.json()] == [created["id"]]
    assert auth.credential_id.hex() not in listed.text


async def test_the_registration_nonce_is_single_use(
    db_client: AsyncClient, member_token: str, totp_on: list[str], app_url, fake_redis
) -> None:
    opts = await db_client.post(OPTIONS, headers=_auth(member_token), json={"code": _code(totp_on)})
    nonce, options = opts.json()["nonce"], opts.json()["options"]
    first = FakeAuthenticator(RP_ID, ORIGIN).register(options)
    second = FakeAuthenticator(RP_ID, ORIGIN).register(options)
    assert (
        await db_client.post(
            PASSKEYS,
            headers=_auth(member_token),
            json={"nonce": nonce, "name": "a", "credential": first},
        )
    ).status_code == 201
    again = await db_client.post(
        PASSKEYS,
        headers=_auth(member_token),
        json={"nonce": nonce, "name": "b", "credential": second},
    )
    assert again.status_code == 400
    assert "could not be added" in again.json()["detail"]


async def test_a_nonce_issued_to_another_user_is_refused(
    db_client: AsyncClient,
    member_token: str,
    admin_token: str,
    totp_on: list[str],
    app_url,
    fake_redis,
    session_factory,
    admin_user: User,
) -> None:
    # The member asks for options; the admin (with 2FA on too) tries to spend them.
    async with session_factory() as db:
        admin = await db.get(User, admin_user.id)
        raw = await totp.start_enrolment(db, admin)
        await totp.confirm_enrolment(db, admin, pyotp.TOTP(raw).now())
    opts = await db_client.post(OPTIONS, headers=_auth(member_token), json={"code": _code(totp_on)})
    cred = FakeAuthenticator(RP_ID, ORIGIN).register(opts.json()["options"])
    resp = await db_client.post(
        PASSKEYS,
        headers=_auth(admin_token),
        json={"nonce": opts.json()["nonce"], "name": "x", "credential": cred},
    )
    assert resp.status_code == 400


async def test_a_bad_attestation_is_400(
    db_client: AsyncClient, member_token: str, totp_on: list[str], app_url, fake_redis
) -> None:
    opts = await db_client.post(OPTIONS, headers=_auth(member_token), json={"code": _code(totp_on)})
    cred = FakeAuthenticator(RP_ID, "http://evil.example").register(opts.json()["options"])
    resp = await db_client.post(
        PASSKEYS,
        headers=_auth(member_token),
        json={"nonce": opts.json()["nonce"], "name": "x", "credential": cred},
    )
    assert resp.status_code == 400


async def test_the_cap_is_409(
    db_client: AsyncClient,
    member_token: str,
    totp_on: list[str],
    app_url,
    fake_redis,
    session_factory,
    member_user: User,
) -> None:
    async with session_factory() as db:
        user = await db.get(User, member_user.id)
        for i in range(passkeys.MAX_PASSKEYS):
            await passkeys.add(
                db, user, passkeys.Registered(f"k{i}".encode(), b"k", 0, []), name="x"
            )
    resp = await db_client.post(OPTIONS, headers=_auth(member_token), json={"code": _code(totp_on)})
    assert resp.status_code == 409
    assert "up to 10" in resp.json()["detail"]


async def test_register_is_audited(
    db_client: AsyncClient,
    member_token: str,
    totp_on: list[str],
    app_url,
    fake_redis,
    session_factory,
    member_user: User,
) -> None:
    from app.models.audit_log import AuditLog
    from sqlalchemy import select

    await _register(db_client, member_token, totp_on)
    async with session_factory() as db:
        rows = (
            (await db.execute(select(AuditLog).where(AuditLog.object_id == member_user.id)))
            .scalars()
            .all()
        )
    assert any(r.meta.get("passkey") == "added" and r.meta.get("name") == "MacBook" for r in rows)


# --- remove --------------------------------------------------------------------------


async def test_remove_needs_a_code_and_is_scoped(
    db_client: AsyncClient,
    member_token: str,
    admin_token: str,
    totp_on: list[str],
    app_url,
    fake_redis,
) -> None:
    _, created = await _register(db_client, member_token, totp_on)
    pid = created["id"]

    wrong = await db_client.post(
        f"{PASSKEYS}/{pid}/remove", headers=_auth(member_token), json={"code": "000000"}
    )
    assert wrong.status_code == 400

    other = await db_client.post(
        f"{PASSKEYS}/{pid}/remove", headers=_auth(admin_token), json={"code": "000000"}
    )
    assert other.status_code == 409  # admin has no 2FA; the gate comes first

    ok = await db_client.post(
        f"{PASSKEYS}/{pid}/remove", headers=_auth(member_token), json={"code": _code(totp_on)}
    )
    assert ok.status_code == 204, ok.text
    assert (await db_client.get(PASSKEYS, headers=_auth(member_token))).json() == []


async def test_remove_unknown_is_404(
    db_client: AsyncClient, member_token: str, totp_on: list[str], app_url, fake_redis
) -> None:
    resp = await db_client.post(
        f"{PASSKEYS}/999/remove", headers=_auth(member_token), json={"code": _code(totp_on)}
    )
    assert resp.status_code == 404


# --- login -----------------------------------------------------------------------------


async def _challenge(db_client: AsyncClient, user: User) -> dict:
    resp = await db_client.post(LOGIN, json={"email": user.email, "password": "memberpass123"})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _mfa_options(db_client: AsyncClient, mfa_token: str) -> dict:
    resp = await db_client.post(MFA_OPTIONS, json={"mfa_token": mfa_token})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_login_advertises_the_methods(
    db_client: AsyncClient,
    member_token: str,
    member_user: User,
    totp_on: list[str],
    app_url,
    fake_redis,
) -> None:
    assert (await _challenge(db_client, member_user))["methods"] == ["totp"]
    await _register(db_client, member_token, totp_on)
    assert (await _challenge(db_client, member_user))["methods"] == ["totp", "passkey"]


async def test_sign_in_with_a_passkey(
    db_client: AsyncClient,
    member_token: str,
    member_user: User,
    totp_on: list[str],
    app_url,
    fake_redis,
) -> None:
    auth, _ = await _register(db_client, member_token, totp_on)
    mfa_token = (await _challenge(db_client, member_user))["mfa_token"]

    opts = await _mfa_options(db_client, mfa_token)
    assert len(opts["options"]["allowCredentials"]) == 1

    resp = await db_client.post(
        MFA_VERIFY,
        json={
            "mfa_token": mfa_token,
            "nonce": opts["nonce"],
            "credential": auth.assert_(opts["options"], count=3),
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["recovery_codes_remaining"] is None
    me = await db_client.get(ME, headers=_auth(resp.json()["access_token"]))
    assert me.json()["email"] == member_user.email

    listed = await db_client.get(PASSKEYS, headers=_auth(member_token))
    assert listed.json()[0]["last_used_at"] is not None


async def test_the_assertion_nonce_is_single_use(
    db_client: AsyncClient,
    member_token: str,
    member_user: User,
    totp_on: list[str],
    app_url,
    fake_redis,
) -> None:
    auth, _ = await _register(db_client, member_token, totp_on)
    mfa_token = (await _challenge(db_client, member_user))["mfa_token"]
    opts = await _mfa_options(db_client, mfa_token)
    body = {
        "mfa_token": mfa_token,
        "nonce": opts["nonce"],
        "credential": auth.assert_(opts["options"], count=1),
    }
    assert (await db_client.post(MFA_VERIFY, json=body)).status_code == 200
    replay = await db_client.post(MFA_VERIFY, json=body)
    assert replay.status_code == 401
    assert replay.json()["detail"] == "That passkey was not accepted."


async def test_a_regressed_count_is_401(
    db_client: AsyncClient,
    member_token: str,
    member_user: User,
    totp_on: list[str],
    app_url,
    fake_redis,
) -> None:
    auth, _ = await _register(db_client, member_token, totp_on)
    mfa_token = (await _challenge(db_client, member_user))["mfa_token"]
    opts = await _mfa_options(db_client, mfa_token)
    first = await db_client.post(
        MFA_VERIFY,
        json={
            "mfa_token": mfa_token,
            "nonce": opts["nonce"],
            "credential": auth.assert_(opts["options"], count=5),
        },
    )
    assert first.status_code == 200

    mfa_token = (await _challenge(db_client, member_user))["mfa_token"]
    opts = await _mfa_options(db_client, mfa_token)
    resp = await db_client.post(
        MFA_VERIFY,
        json={
            "mfa_token": mfa_token,
            "nonce": opts["nonce"],
            "credential": auth.assert_(opts["options"], count=5),
        },
    )
    assert resp.status_code == 401


async def test_every_login_refusal_shares_one_message(
    db_client: AsyncClient,
    member_token: str,
    member_user: User,
    totp_on: list[str],
    app_url,
    fake_redis,
) -> None:
    await _register(db_client, member_token, totp_on)
    bad_token = await db_client.post(MFA_OPTIONS, json={"mfa_token": "nope"})
    mfa_token = (await _challenge(db_client, member_user))["mfa_token"]
    opts = await _mfa_options(db_client, mfa_token)
    stranger = FakeAuthenticator(RP_ID, ORIGIN)  # a key we never registered
    unknown = await db_client.post(
        MFA_VERIFY,
        json={
            "mfa_token": mfa_token,
            "nonce": opts["nonce"],
            "credential": stranger.assert_(opts["options"], count=1),
        },
    )
    assert bad_token.status_code == unknown.status_code == 401
    assert bad_token.json() == unknown.json() == {"detail": "That passkey was not accepted."}


async def test_options_without_passkeys_is_401(
    db_client: AsyncClient, member_user: User, totp_on: list[str], app_url, fake_redis
) -> None:
    mfa_token = (await _challenge(db_client, member_user))["mfa_token"]
    assert (await db_client.post(MFA_OPTIONS, json={"mfa_token": mfa_token})).status_code == 401


async def test_login_options_are_rate_limited(
    db_client: AsyncClient,
    member_token: str,
    member_user: User,
    totp_on: list[str],
    app_url,
    fake_redis,
) -> None:
    await _register(db_client, member_token, totp_on)
    mfa_token = (await _challenge(db_client, member_user))["mfa_token"]
    for _ in range(rate_limit.MFA_ATTEMPT_LIMIT):
        assert (await db_client.post(MFA_OPTIONS, json={"mfa_token": mfa_token})).status_code == 200
    assert (await db_client.post(MFA_OPTIONS, json={"mfa_token": mfa_token})).status_code == 429


async def test_a_version_bump_kills_the_passkey_challenge_too(
    db_client: AsyncClient,
    member_token: str,
    member_user: User,
    totp_on: list[str],
    app_url,
    fake_redis,
    session_factory,
) -> None:
    from app.services import user as user_service

    auth, _ = await _register(db_client, member_token, totp_on)
    mfa_token = (await _challenge(db_client, member_user))["mfa_token"]
    opts = await _mfa_options(db_client, mfa_token)
    async with session_factory() as db:
        await user_service.set_password(db, await db.get(User, member_user.id), "changed12345")
    resp = await db_client.post(
        MFA_VERIFY,
        json={
            "mfa_token": mfa_token,
            "nonce": opts["nonce"],
            "credential": auth.assert_(opts["options"], count=1),
        },
    )
    assert resp.status_code == 401


# --- the backstop clears passkeys ----------------------------------------------------


async def test_admin_disable_removes_passkeys(
    db_client: AsyncClient,
    member_token: str,
    admin_token: str,
    member_user: User,
    totp_on: list[str],
    app_url,
    fake_redis,
    monkeypatch,
) -> None:
    from app.api.routes import users as users_routes

    class Quiet:
        def delay(self, **kwargs) -> None:
            pass

    monkeypatch.setattr(users_routes, "send_email_task", Quiet())
    await _register(db_client, member_token, totp_on)
    resp = await db_client.post(
        f"/api/v1/users/{member_user.id}/totp/disable", headers=_auth(admin_token)
    )
    assert resp.status_code == 204
    assert (await _challenge(db_client, member_user)).get("methods") is None  # plain token pair
