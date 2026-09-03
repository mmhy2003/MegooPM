"""Invite, resend, accept. SQLite-backed; Redis and the mail task are faked."""

from __future__ import annotations

import pytest
from app.api.routes import users as users_routes
from app.models.audit_log import AuditLog
from app.models.enums import AuthTokenKind, SmtpSecurity
from app.models.instance_settings import InstanceSettings
from app.models.user import User
from app.services import rate_limit
from app.services.auth_tokens import INVITE_TTL, issue
from httpx import AsyncClient
from sqlalchemy import select

INVITE = "/api/v1/users/invite"
ACCEPT = "/api/v1/auth/accept-invite"
LOGIN = "/api/v1/auth/login"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key: str, seconds: int) -> bool:
        return True

    async def ttl(self, key: str) -> int:
        return 3600

    async def aclose(self) -> None:
        pass


class RecordingTask:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def delay(self, **kwargs) -> None:
        self.calls.append(kwargs)


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    client = FakeRedis()
    monkeypatch.setattr(rate_limit, "_client", lambda: client)
    return client


@pytest.fixture
def mail(monkeypatch: pytest.MonkeyPatch) -> RecordingTask:
    task = RecordingTask()
    monkeypatch.setattr(users_routes, "send_email_task", task)
    return task


@pytest.fixture
async def mail_configured(session_factory) -> None:
    async with session_factory() as db:
        db.add(
            InstanceSettings(
                id=1,
                smtp_enabled=True,
                smtp_host="mail.example.com",
                smtp_port=587,
                smtp_security=SmtpSecurity.starttls,
                smtp_from="megoopm@example.com",
                app_url="https://pm.example.com",
            )
        )
        await db.commit()


@pytest.fixture
async def mail_unconfigured(session_factory) -> None:
    async with session_factory() as db:
        db.add(InstanceSettings(id=1))
        await db.commit()


async def _invite(client: AsyncClient, token: str, email: str, **over) -> dict:
    body = {"email": email, "full_name": "", "role": "member"} | over
    resp = await client.post(INVITE, headers=_auth(token), json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- POST /users/invite ----------------------------------------------------


async def test_invite_creates_an_invited_row_and_queues_the_email(
    db_client: AsyncClient, admin_token: str, admin_user: User, mail_configured, mail
) -> None:
    body = await _invite(db_client, admin_token, "new@example.com", role="admin")

    assert body["is_active"] is False
    assert body["invited_at"] is not None
    assert body["role"] == "admin"

    assert len(mail.calls) == 1
    call = mail.calls[0]
    assert call["to"] == "new@example.com"
    assert call["template"] == "invitation"
    assert call["context"]["accept_url"].startswith("https://pm.example.com/accept-invite?token=")
    assert call["context"]["ttl_days"] == 7
    # The inviter is named: an invitation with no human attached is phishing.
    # The admin fixture's full_name is "Admin User"; the email is the fallback.
    assert call["context"]["inviter_name"] == "Admin User"


async def test_invite_is_admin_only(
    db_client: AsyncClient, member_token: str, mail_configured, mail
) -> None:
    resp = await db_client.post(
        INVITE, headers=_auth(member_token), json={"email": "x@example.com", "role": "member"}
    )
    assert resp.status_code == 403


@pytest.mark.parametrize("state", ["active", "inactive", "invited"])
async def test_a_taken_address_is_409_in_every_state(
    db_client: AsyncClient, admin_token: str, mail_configured, mail, session_factory, state: str
) -> None:
    email = "taken@example.com"
    if state == "invited":
        await _invite(db_client, admin_token, email)
    else:
        resp = await db_client.post(
            "/api/v1/users",
            headers=_auth(admin_token),
            json={
                "email": email,
                "password": "password123",
                "role": "member",
                "is_active": state == "active",
            },
        )
        assert resp.status_code == 201, resp.text

    resp = await db_client.post(
        INVITE, headers=_auth(admin_token), json={"email": email.upper(), "role": "member"}
    )
    assert resp.status_code == 409


async def test_invite_is_refused_when_mail_is_not_configured(
    db_client: AsyncClient, admin_token: str, mail_unconfigured, mail
) -> None:
    # Belt to the hidden button's braces.
    resp = await db_client.post(
        INVITE, headers=_auth(admin_token), json={"email": "new@example.com", "role": "member"}
    )
    assert resp.status_code == 409
    assert mail.calls == []


async def test_invite_writes_an_audit_row(
    db_client: AsyncClient,
    admin_token: str,
    admin_user: User,
    mail_configured,
    mail,
    session_factory,
) -> None:
    body = await _invite(db_client, admin_token, "new@example.com")
    async with session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(AuditLog).where(
                        AuditLog.object_type == "user", AuditLog.object_id == body["id"]
                    )
                )
            )
            .scalars()
            .all()
        )
    assert any(r.meta.get("invited") for r in rows)
    assert rows[0].actor == admin_user.email


# --- POST /users/{id}/invite (resend) ---------------------------------------


async def test_resend_queues_a_fresh_email(
    db_client: AsyncClient, admin_token: str, mail_configured, mail
) -> None:
    body = await _invite(db_client, admin_token, "new@example.com")
    first_url = mail.calls[0]["context"]["accept_url"]

    resp = await db_client.post(f"/api/v1/users/{body['id']}/invite", headers=_auth(admin_token))

    assert resp.status_code == 204, resp.text
    assert len(mail.calls) == 2
    # A new token every time; the old one is superseded (Task 1 proves that).
    assert mail.calls[1]["context"]["accept_url"] != first_url


async def test_resend_is_refused_for_an_accepted_user(
    db_client: AsyncClient, admin_token: str, admin_user: User, mail_configured, mail
) -> None:
    # It would hand anyone with their inbox a way to reset their password.
    resp = await db_client.post(
        f"/api/v1/users/{admin_user.id}/invite", headers=_auth(admin_token)
    )
    assert resp.status_code == 409
    assert mail.calls == []


async def test_resend_404s_for_an_unknown_user(
    db_client: AsyncClient, admin_token: str, mail_configured, mail
) -> None:
    resp = await db_client.post("/api/v1/users/999999/invite", headers=_auth(admin_token))
    assert resp.status_code == 404


# --- POST /auth/accept-invite -----------------------------------------------


def _token_from(mail: RecordingTask) -> str:
    return mail.calls[-1]["context"]["accept_url"].split("token=")[1]


async def test_accept_activates_and_the_new_password_works(
    db_client: AsyncClient, admin_token: str, mail_configured, mail, fake_redis
) -> None:
    body = await _invite(db_client, admin_token, "new@example.com")
    raw = _token_from(mail)

    resp = await db_client.post(
        ACCEPT, json={"token": raw, "full_name": "New Person", "password": "chosen12345"}
    )
    assert resp.status_code == 204, resp.text

    login = await db_client.post(
        LOGIN, json={"email": "new@example.com", "password": "chosen12345"}
    )
    assert login.status_code == 200, login.text

    me = await db_client.get("/api/v1/users/me", headers=_auth(login.json()["access_token"]))
    assert me.json()["full_name"] == "New Person"
    assert me.json()["invited_at"] is None
    assert me.json()["is_active"] is True
    assert me.json()["id"] == body["id"]


async def test_accept_spends_the_token(
    db_client: AsyncClient, admin_token: str, mail_configured, mail, fake_redis
) -> None:
    await _invite(db_client, admin_token, "new@example.com")
    raw = _token_from(mail)
    await db_client.post(ACCEPT, json={"token": raw, "full_name": "N", "password": "chosen12345"})

    again = await db_client.post(
        ACCEPT, json={"token": raw, "full_name": "N", "password": "other12345"}
    )
    assert again.status_code == 400


async def test_accept_refuses_a_reset_token(
    db_client: AsyncClient, admin_token: str, mail_configured, mail, fake_redis, session_factory
) -> None:
    body = await _invite(db_client, admin_token, "new@example.com")
    async with session_factory() as db:
        user = await db.get(User, body["id"])
        raw = await issue(db, user=user, kind=AuthTokenKind.password_reset, ttl=INVITE_TTL)

    resp = await db_client.post(
        ACCEPT, json={"token": raw, "full_name": "N", "password": "chosen12345"}
    )
    assert resp.status_code == 400


async def test_accept_is_rate_limited_per_ip(
    db_client: AsyncClient, mail_configured, fake_redis
) -> None:
    for _ in range(rate_limit.RESET_IP_LIMIT):
        resp = await db_client.post(
            ACCEPT, json={"token": "nope", "full_name": "N", "password": "x" * 12}
        )
        assert resp.status_code == 400
    resp = await db_client.post(
        ACCEPT, json={"token": "nope", "full_name": "N", "password": "x" * 12}
    )
    assert resp.status_code == 429


async def test_accept_writes_an_audit_row_with_the_new_user_as_actor(
    db_client: AsyncClient, admin_token: str, mail_configured, mail, fake_redis, session_factory
) -> None:
    body = await _invite(db_client, admin_token, "new@example.com")
    raw = _token_from(mail)
    await db_client.post(ACCEPT, json={"token": raw, "full_name": "N", "password": "chosen12345"})

    async with session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(AuditLog).where(
                        AuditLog.object_type == "user", AuditLog.object_id == body["id"]
                    )
                )
            )
            .scalars()
            .all()
        )
    assert any(r.meta.get("invitation_accepted") and r.actor == "new@example.com" for r in rows)


async def test_a_short_password_is_refused_before_the_token_is_spent(
    db_client: AsyncClient, admin_token: str, mail_configured, mail, fake_redis
) -> None:
    await _invite(db_client, admin_token, "new@example.com")
    raw = _token_from(mail)

    resp = await db_client.post(ACCEPT, json={"token": raw, "full_name": "N", "password": "short"})
    assert resp.status_code == 422

    resp = await db_client.post(
        ACCEPT, json={"token": raw, "full_name": "N", "password": "chosen12345"}
    )
    assert resp.status_code == 204
