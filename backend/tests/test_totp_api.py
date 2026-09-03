"""Enrol, confirm, regenerate, disable, and the admin backstop."""

from __future__ import annotations

import time

import pyotp
import pytest
from app.api.routes import users as users_routes
from app.models.audit_log import AuditLog
from app.models.user import User
from app.services import totp
from httpx import AsyncClient
from sqlalchemy import select

SETUP = "/api/v1/users/me/totp/setup"
ENABLE = "/api/v1/users/me/totp/enable"
DISABLE = "/api/v1/users/me/totp/disable"
CODES = "/api/v1/users/me/totp/recovery-codes"
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _next_code(secret: str) -> str:
    """The code for the *next* step; the enrolment code is a replay."""
    return pyotp.TOTP(secret).at(int(time.time()) + 30)


class RecordingTask:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def delay(self, **kwargs) -> None:
        self.calls.append(kwargs)


@pytest.fixture
def mail(monkeypatch: pytest.MonkeyPatch) -> RecordingTask:
    task = RecordingTask()
    monkeypatch.setattr(users_routes, "send_email_task", task)
    return task


async def _enable(db_client: AsyncClient, token: str) -> tuple[str, list[str]]:
    setup = await db_client.post(SETUP, headers=_auth(token))
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    enable = await db_client.post(
        ENABLE, headers=_auth(token), json={"code": pyotp.TOTP(secret).now()}
    )
    assert enable.status_code == 200, enable.text
    return secret, enable.json()["codes"]


# --- setup + enable --------------------------------------------------------


async def test_setup_returns_a_secret_and_a_uri_and_leaves_2fa_off(
    db_client: AsyncClient, member_token: str, member_user: User
) -> None:
    resp = await db_client.post(SETUP, headers=_auth(member_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["secret"]) == 32
    assert body["otpauth_uri"].startswith("otpauth://totp/MegooPM:")
    assert member_user.email.replace("@", "%40") in body["otpauth_uri"]

    me = await db_client.get("/api/v1/users/me", headers=_auth(member_token))
    assert me.json()["totp_enabled"] is False


async def test_enable_with_the_right_code_returns_ten_codes_once(
    db_client: AsyncClient, member_token: str
) -> None:
    _, codes = await _enable(db_client, member_token)
    assert len(codes) == 10
    assert all(len(c) == 11 and c[5] == "-" for c in codes)


async def test_enable_with_a_wrong_code_is_400_and_stays_pending(
    db_client: AsyncClient, member_token: str
) -> None:
    await db_client.post(SETUP, headers=_auth(member_token))
    resp = await db_client.post(ENABLE, headers=_auth(member_token), json={"code": "000000"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == totp.INVALID_CODE_MESSAGE

    me = await db_client.get("/api/v1/users/me", headers=_auth(member_token))
    assert me.json()["totp_enabled"] is False


async def test_enable_with_nothing_pending_is_409(
    db_client: AsyncClient, member_token: str
) -> None:
    resp = await db_client.post(ENABLE, headers=_auth(member_token), json={"code": "000000"})
    assert resp.status_code == 409


async def test_setup_while_enabled_is_409(db_client: AsyncClient, member_token: str) -> None:
    await _enable(db_client, member_token)
    resp = await db_client.post(SETUP, headers=_auth(member_token))
    assert resp.status_code == 409


async def test_user_read_never_carries_the_secret(
    db_client: AsyncClient, member_token: str
) -> None:
    secret, codes = await _enable(db_client, member_token)
    me = await db_client.get("/api/v1/users/me", headers=_auth(member_token))
    assert me.json()["totp_enabled"] is True
    assert secret not in me.text
    assert all(c not in me.text for c in codes)
    assert "totp_secret" not in me.text and "last_step" not in me.text


async def test_enable_ends_other_sessions(
    db_client: AsyncClient, member_token: str, member_user: User
) -> None:
    other = await db_client.post(
        LOGIN, json={"email": member_user.email, "password": "memberpass123"}
    )
    await _enable(db_client, member_token)
    resp = await db_client.post(REFRESH, json={"refresh_token": other.json()["refresh_token"]})
    assert resp.status_code == 401


async def test_enable_is_audited(
    db_client: AsyncClient, member_token: str, member_user: User, session_factory
) -> None:
    await _enable(db_client, member_token)
    async with session_factory() as db:
        rows = (
            (await db.execute(select(AuditLog).where(AuditLog.object_id == member_user.id)))
            .scalars()
            .all()
        )
    assert any(r.meta.get("totp") == "enabled" for r in rows)


# --- recovery codes ---------------------------------------------------------


async def test_regenerate_requires_a_valid_code(db_client: AsyncClient, member_token: str) -> None:
    secret, old = await _enable(db_client, member_token)

    wrong = await db_client.post(CODES, headers=_auth(member_token), json={"code": "000000"})
    assert wrong.status_code == 400

    # The enrolment code is a replay now; use the next step.
    ok = await db_client.post(CODES, headers=_auth(member_token), json={"code": _next_code(secret)})
    assert ok.status_code == 200, ok.text
    new = ok.json()["codes"]
    assert len(new) == 10 and set(new).isdisjoint(old)


# --- self-disable -------------------------------------------------------------


async def test_self_disable_needs_a_code(db_client: AsyncClient, member_token: str) -> None:
    await _enable(db_client, member_token)
    resp = await db_client.post(DISABLE, headers=_auth(member_token), json={})
    assert resp.status_code == 422


async def test_self_disable_with_a_wrong_code_is_400(
    db_client: AsyncClient, member_token: str
) -> None:
    await _enable(db_client, member_token)
    resp = await db_client.post(DISABLE, headers=_auth(member_token), json={"code": "000000"})
    assert resp.status_code == 400


async def test_self_disable_with_a_recovery_code_turns_it_off(
    db_client: AsyncClient, member_token: str
) -> None:
    _, codes = await _enable(db_client, member_token)
    resp = await db_client.post(DISABLE, headers=_auth(member_token), json={"code": codes[0]})
    assert resp.status_code == 204, resp.text
    me = await db_client.get("/api/v1/users/me", headers=_auth(member_token))
    assert me.json()["totp_enabled"] is False


async def test_self_disable_when_off_is_409(db_client: AsyncClient, member_token: str) -> None:
    resp = await db_client.post(DISABLE, headers=_auth(member_token), json={"code": "000000"})
    assert resp.status_code == 409


# --- admin disable --------------------------------------------------------------


async def test_admin_disable_needs_no_code_and_sends_the_email(
    db_client: AsyncClient,
    admin_token: str,
    admin_user: User,
    member_token: str,
    member_user: User,
    mail,
) -> None:
    await _enable(db_client, member_token)

    resp = await db_client.post(
        f"/api/v1/users/{member_user.id}/totp/disable", headers=_auth(admin_token)
    )

    assert resp.status_code == 204, resp.text
    # Their next sign-in has no code step.
    login = await db_client.post(
        LOGIN, json={"email": member_user.email, "password": "memberpass123"}
    )
    assert "access_token" in login.json()

    assert len(mail.calls) == 1
    assert mail.calls[0]["to"] == member_user.email
    assert mail.calls[0]["template"] == "totp_disabled"
    assert mail.calls[0]["context"]["admin_name"] == "Admin User"


async def test_admin_disable_is_admin_only(
    db_client: AsyncClient, member_token: str, member_user: User, admin_user: User, mail
) -> None:
    resp = await db_client.post(
        f"/api/v1/users/{admin_user.id}/totp/disable", headers=_auth(member_token)
    )
    assert resp.status_code == 403


async def test_admin_disable_when_off_is_409(
    db_client: AsyncClient, admin_token: str, member_user: User, mail
) -> None:
    resp = await db_client.post(
        f"/api/v1/users/{member_user.id}/totp/disable", headers=_auth(admin_token)
    )
    assert resp.status_code == 409
    assert mail.calls == []


async def test_admin_disable_is_audited_naming_the_admin(
    db_client: AsyncClient,
    admin_token: str,
    admin_user: User,
    member_token: str,
    member_user: User,
    mail,
    session_factory,
) -> None:
    await _enable(db_client, member_token)
    await db_client.post(f"/api/v1/users/{member_user.id}/totp/disable", headers=_auth(admin_token))
    async with session_factory() as db:
        rows = (
            (await db.execute(select(AuditLog).where(AuditLog.object_id == member_user.id)))
            .scalars()
            .all()
        )
    assert any(
        r.meta.get("totp") == "disabled_by_admin" and r.actor == admin_user.email for r in rows
    )
