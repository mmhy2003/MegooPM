"""The three password-reset routes. SQLite-backed; Redis and SMTP are faked."""

from __future__ import annotations

import pytest
from app.api.routes import auth as auth_routes
from app.models.auth_token import AuthToken
from app.models.enums import AuthTokenKind, SmtpSecurity
from app.models.instance_settings import InstanceSettings
from app.models.user import User
from app.services import rate_limit
from app.services.auth_tokens import RESET_TTL, hash_token, issue
from httpx import AsyncClient
from sqlalchemy import select

FORGOT = "/api/v1/auth/forgot-password"
RESET = "/api/v1/auth/reset-password"
CAPABILITIES = "/api/v1/auth/capabilities"
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"


class FakeRedis:
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
        return self.ttls.get(key, 3600)

    async def aclose(self) -> None:
        pass


class RecordingTask:
    """Stands in for the Celery task: records .delay() calls, sends nothing.

    Tests run with CELERY_TASK_ALWAYS_EAGER, so the real task would execute
    inline and try to open an SMTP connection.
    """

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
    monkeypatch.setattr(auth_routes, "send_email_task", task)
    return task


@pytest.fixture
async def mail_configured(session_factory) -> None:
    """A settings row with SMTP on and an app URL, so reset is available."""
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


# --- capabilities ----------------------------------------------------------


async def test_capabilities_true_when_smtp_and_app_url_are_set(
    db_client: AsyncClient, mail_configured
) -> None:
    resp = await db_client.get(CAPABILITIES)
    assert resp.status_code == 200
    assert resp.json()["password_reset"] is True


async def test_capabilities_false_without_smtp(db_client: AsyncClient, mail_unconfigured) -> None:
    resp = await db_client.get(CAPABILITIES)
    assert resp.json()["password_reset"] is False


async def test_capabilities_needs_no_auth(db_client: AsyncClient, mail_configured) -> None:
    # The login page reads it before anyone is signed in.
    resp = await db_client.get(CAPABILITIES)
    assert resp.status_code == 200


# --- forgot-password -------------------------------------------------------


async def test_a_registered_address_gets_a_token_and_an_email(
    db_client: AsyncClient, admin_user: User, mail_configured, fake_redis, mail, session_factory
) -> None:
    resp = await db_client.post(FORGOT, json={"email": admin_user.email})

    assert resp.status_code == 202, resp.text
    assert len(mail.calls) == 1
    assert mail.calls[0]["to"] == admin_user.email
    assert mail.calls[0]["template"] == "password_reset"
    reset_url = mail.calls[0]["context"]["reset_url"]
    assert reset_url.startswith("https://pm.example.com/reset-password?token=")

    async with session_factory() as db:
        rows = (await db.execute(select(AuthToken))).scalars().all()
    assert len(rows) == 1
    # The raw token in the link must hash to the stored row.
    raw = reset_url.split("token=")[1]
    assert rows[0].token_hash == hash_token(raw)


async def test_an_unknown_address_gets_the_same_response_and_no_email(
    db_client: AsyncClient, mail_configured, fake_redis, mail
) -> None:
    resp = await db_client.post(FORGOT, json={"email": "nobody@example.com"})

    assert resp.status_code == 202, resp.text
    assert mail.calls == []


async def test_the_response_is_byte_identical_for_known_and_unknown(
    db_client: AsyncClient, admin_user: User, mail_configured, fake_redis, mail
) -> None:
    # Otherwise the login page is a directory of who has an account.
    known = await db_client.post(FORGOT, json={"email": admin_user.email})
    unknown = await db_client.post(FORGOT, json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code
    assert known.content == unknown.content


async def test_an_inactive_account_gets_the_neutral_response_and_no_email(
    db_client: AsyncClient, member_user: User, mail_configured, fake_redis, mail, session_factory
) -> None:
    async with session_factory() as db:
        user = await db.get(User, member_user.id)
        user.is_active = False
        await db.commit()

    resp = await db_client.post(FORGOT, json={"email": member_user.email})

    assert resp.status_code == 202
    assert mail.calls == []


async def test_the_address_is_matched_case_insensitively(
    db_client: AsyncClient, admin_user: User, mail_configured, fake_redis, mail
) -> None:
    resp = await db_client.post(FORGOT, json={"email": admin_user.email.upper()})
    assert resp.status_code == 202
    assert len(mail.calls) == 1


async def test_with_mail_unconfigured_nothing_is_queued(
    db_client: AsyncClient, admin_user: User, mail_unconfigured, fake_redis, mail
) -> None:
    # Still the neutral 202: the capabilities endpoint gates the UI, and the
    # route must not become a different oracle.
    resp = await db_client.post(FORGOT, json={"email": admin_user.email})
    assert resp.status_code == 202
    assert mail.calls == []


async def test_the_fourth_request_for_one_address_is_429(
    db_client: AsyncClient, admin_user: User, mail_configured, fake_redis, mail
) -> None:
    for _ in range(3):
        assert (await db_client.post(FORGOT, json={"email": admin_user.email})).status_code == 202
    resp = await db_client.post(FORGOT, json={"email": admin_user.email})
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    assert len(mail.calls) == 3


async def test_redis_down_is_503_not_202(
    db_client: AsyncClient, admin_user: User, mail_configured, mail, monkeypatch
) -> None:
    from redis.exceptions import ConnectionError as RedisConnectionError

    class Dead:
        async def incr(self, key):
            raise RedisConnectionError("refused")

        async def aclose(self):
            pass

    monkeypatch.setattr(rate_limit, "_client", lambda: Dead())
    resp = await db_client.post(FORGOT, json={"email": admin_user.email})
    assert resp.status_code == 503
    assert mail.calls == []


# --- reset-password --------------------------------------------------------


async def _issue_for(session_factory, user: User) -> str:
    async with session_factory() as db:
        u = await db.get(User, user.id)
        return await issue(db, user=u, kind=AuthTokenKind.password_reset, ttl=RESET_TTL)


async def test_a_valid_token_sets_the_password_and_ends_sessions(
    db_client: AsyncClient, admin_user: User, mail_configured, fake_redis, mail, session_factory
) -> None:
    before = await db_client.post(
        LOGIN, json={"email": admin_user.email, "password": "adminpass123"}
    )
    raw = await _issue_for(session_factory, admin_user)

    resp = await db_client.post(RESET, json={"token": raw, "new_password": "brandnew12345"})

    assert resp.status_code == 204, resp.text
    # New password works.
    after = await db_client.post(
        LOGIN, json={"email": admin_user.email, "password": "brandnew12345"}
    )
    assert after.status_code == 200
    # Old one does not.
    old = await db_client.post(LOGIN, json={"email": admin_user.email, "password": "adminpass123"})
    assert old.status_code == 401
    # The session from before the reset is dead.
    stale = await db_client.post(REFRESH, json={"refresh_token": before.json()["refresh_token"]})
    assert stale.status_code == 401


async def test_a_successful_reset_sends_the_changed_notice(
    db_client: AsyncClient, admin_user: User, mail_configured, fake_redis, mail, session_factory
) -> None:
    raw = await _issue_for(session_factory, admin_user)
    await db_client.post(RESET, json={"token": raw, "new_password": "brandnew12345"})

    assert any(c["template"] == "password_changed" for c in mail.calls)


async def test_a_reset_is_audited(
    db_client: AsyncClient, admin_user: User, mail_configured, fake_redis, mail, session_factory
) -> None:
    raw = await _issue_for(session_factory, admin_user)
    await db_client.post(RESET, json={"token": raw, "new_password": "brandnew12345"})

    # A fresh token: the reset ended the one we had.
    fresh = await db_client.post(
        LOGIN, json={"email": admin_user.email, "password": "brandnew12345"}
    )
    audit = await db_client.get(
        "/api/v1/audit-log?object_type=user",
        headers={"Authorization": f"Bearer {fresh.json()['access_token']}"},
    )
    entries = audit.json()["items"]
    assert any(e["meta"].get("password_reset_via_email") for e in entries)


async def test_a_used_token_is_refused(
    db_client: AsyncClient, admin_user: User, mail_configured, fake_redis, mail, session_factory
) -> None:
    raw = await _issue_for(session_factory, admin_user)
    await db_client.post(RESET, json={"token": raw, "new_password": "brandnew12345"})

    resp = await db_client.post(RESET, json={"token": raw, "new_password": "another12345"})
    assert resp.status_code == 400


async def test_an_unknown_token_gets_the_same_message_as_a_used_one(
    db_client: AsyncClient, admin_user: User, mail_configured, fake_redis, mail, session_factory
) -> None:
    raw = await _issue_for(session_factory, admin_user)
    await db_client.post(RESET, json={"token": raw, "new_password": "brandnew12345"})

    used = await db_client.post(RESET, json={"token": raw, "new_password": "x" * 12})
    unknown = await db_client.post(RESET, json={"token": "nope", "new_password": "x" * 12})
    assert used.status_code == unknown.status_code == 400
    assert used.json() == unknown.json()


async def test_a_short_password_is_refused_before_the_token_is_spent(
    db_client: AsyncClient, admin_user: User, mail_configured, fake_redis, mail, session_factory
) -> None:
    raw = await _issue_for(session_factory, admin_user)

    resp = await db_client.post(RESET, json={"token": raw, "new_password": "short"})
    assert resp.status_code == 422

    # The token is still good: validation failed before anything was spent.
    resp = await db_client.post(RESET, json={"token": raw, "new_password": "brandnew12345"})
    assert resp.status_code == 204
