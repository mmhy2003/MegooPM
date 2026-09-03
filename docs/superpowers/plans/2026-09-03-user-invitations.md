# User Invitations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An admin invites a person by email and role; they follow the link, choose a name and password, and sign in.

**Architecture:** An invited user is a real row from the start — inactive, with an unusable password hash and `invited_at` set — so the address is reserved by the existing unique constraint. The token is P2's `auth_token` table with a new `invitation` kind and a seven-day life. Two admin routes issue and resend; one public route accepts. Revoke is the existing delete.

**Tech Stack:** Everything from P1 and P2. **No new dependencies.**

**Spec:** `docs/superpowers/specs/2026-09-03-user-invitations-design.md`

## Global Constraints

- **`invited_at IS NOT NULL` is the one definition of invited.** No status enum. Never set `is_active = true` on a row that still has `invited_at`.
- **The placeholder password is a real Argon2 hash of 32 random bytes**, so login verifies it like any other and cannot take a timing shortcut. Do not "optimise" it to a sentinel.
- **Seven-day TTL** for invitations, `INVITE_TTL`, beside P2's `RESET_TTL`.
- **409 on a taken address in every state** — active, inactive, or invited. Resend is the fix for "they never got it".
- **Resend is refused for an accepted user** — it would hand anyone with their inbox a password reset.
- **Accept sends the invitee to `/login`, not into a session.**
- **`ALTER TYPE … ADD VALUE` must run in `op.get_context().autocommit_block()`** — Postgres refuses it inside the transaction Alembic wraps every migration in.
- **`UserRead` gains a required `invited_at: datetime | None`.** Every frontend `User` fixture must gain it too; vitest will not catch this, `npm run typecheck` will.
- **Backend tests cannot run natively on Windows** (`fcntl`). Use the container recipe in Task 1, Step 2.
- Frontend commands run from `frontend/`: `npm test`, `npm run typecheck`, `npm run lint`.

## File Structure

**Backend**

| file | responsibility |
| --- | --- |
| `app/models/user.py` | `invited_at` |
| `app/models/enums.py` | `AuthTokenKind.invitation` |
| `alembic/versions/0027_invitations.py` | the column; the enum value in an autocommit block |
| `app/services/auth_tokens.py` | `INVITE_TTL` |
| `app/services/user.py` | `invite_user`, `accept_invitation` |
| `app/services/mail/templates/invitation.{html,txt}.j2` | the email |
| `app/schemas/user.py` | `UserInvite`; `invited_at` on `UserRead` |
| `app/schemas/auth.py` | `AcceptInviteRequest` |
| `app/api/routes/users.py` | `POST /users/invite`, `POST /users/{id}/invite` |
| `app/api/routes/auth.py` | `POST /auth/accept-invite` |

**Frontend**

| file | responsibility |
| --- | --- |
| `src/lib/auth/api.ts` | `acceptInvite` |
| `src/lib/auth/session.ts` | `ACCEPT_INVITE_ROUTE` in `PUBLIC_ROUTES` |
| `src/lib/api/resources/users.ts` | `invite`, `resendInvite`, `UserInvite` |
| `src/components/auth/accept-invite-form.tsx`, `src/app/accept-invite/page.tsx` | the public page |
| `src/components/users/invite-dialog.tsx` | email, optional name, role |
| `src/components/users/users-view.tsx` | button, badge, resend action, delete copy |

---

### Task 1: Inviting and accepting, in the service layer

The row, the column, the enum value, and the two service functions. Routes come
in Task 3; this task is provable on its own through the SQLite session factory.

**Files:**
- Modify: `backend/app/models/user.py`
- Modify: `backend/app/models/enums.py`
- Create: `backend/alembic/versions/0027_invitations.py`
- Modify: `backend/app/services/auth_tokens.py`
- Modify: `backend/app/services/user.py`
- Test: `backend/tests/test_invitations_service.py`

**Interfaces:**
- Consumes: `issue`, `redeem`, `hash_token` from `app.services.auth_tokens`; `hash_password`, `verify_password` from `app.core.security`.
- Produces:
  - `User.invited_at: datetime | None`
  - `AuthTokenKind.invitation`
  - `INVITE_TTL = timedelta(days=7)`
  - `user_service.invite_user(db, *, email: str, full_name: str, role: UserRole) -> User` — raises `EmailAlreadyExistsError`; commits.
  - `user_service.accept_invitation(db, user: User, *, full_name: str, password: str) -> None` — commits.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_invitations_service.py`:

```python
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
        a = await user_service.invite_user(db, email="a@example.com", full_name="", role=UserRole.member)
        b = await user_service.invite_user(db, email="b@example.com", full_name="", role=UserRole.member)
    assert a.hashed_password != b.hashed_password


async def test_a_taken_address_is_refused(session_factory, admin_user: User) -> None:
    async with session_factory() as db:
        with pytest.raises(user_service.EmailAlreadyExistsError):
            await user_service.invite_user(
                db, email=admin_user.email.upper(), full_name="", role=UserRole.member
            )


async def test_an_already_invited_address_is_refused(session_factory) -> None:
    async with session_factory() as db:
        await user_service.invite_user(db, email="new@example.com", full_name="", role=UserRole.member)
        with pytest.raises(user_service.EmailAlreadyExistsError):
            await user_service.invite_user(
                db, email="new@example.com", full_name="", role=UserRole.member
            )


async def test_accepting_sets_everything_and_activates(session_factory) -> None:
    async with session_factory() as db:
        user = await user_service.invite_user(
            db, email="new@example.com", full_name="", role=UserRole.member
        )
        await user_service.accept_invitation(db, user, full_name="New Person", password="chosen12345")

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
```

- [ ] **Step 2: Start the test stack and run the tests to verify they fail**

```bash
export MSYS_NO_PATHCONV=1
docker network create megoopm-testnet 2>/dev/null || true
docker run -d --name megoopm-testdb --network megoopm-testnet \
  -e POSTGRES_USER=megoopm -e POSTGRES_PASSWORD=megoopm -e POSTGRES_DB=megoopm postgres:16-alpine
docker run -d --name megoopm-test --user root --network megoopm-testnet \
  -v "C:/Projects/megoopm/backend:/src" -w /src \
  -e CELERY_TASK_ALWAYS_EAGER=true -e CELERY_RESULT_BACKEND=cache+memory:// \
  -e DATABASE_URL="postgresql+asyncpg://megoopm:megoopm@megoopm-testdb:5432/megoopm" \
  --entrypoint sleep megoopm-backend infinity
docker exec megoopm-test pip install -q "pytest>=8.2" "pytest-asyncio>=0.23" \
  "aiosqlite>=0.20" "ruff>=0.6" "maxminddb"
docker exec megoopm-test python -m pytest tests/test_invitations_service.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — `ImportError: cannot import name 'INVITE_TTL'`.

- [ ] **Step 3: The column and the enum value**

In `backend/app/models/user.py`, add `DateTime` to the `sqlalchemy` import and
`from datetime import datetime` at the top, then after `token_version`:

```python
    # Set while an invitation is outstanding; cleared on accept. This is the
    # one definition of "invited" — no status enum beside is_active, because
    # two sources of truth is how "off" and "invited" drift apart.
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

In `backend/app/models/enums.py`, in `AuthTokenKind`:

```python
    password_reset = "password_reset"
    invitation = "invitation"
```

In `backend/app/services/auth_tokens.py`, beside `RESET_TTL`:

```python
#: A reset is a same-hour action by someone at the keyboard; an invitation is
#: opened when the invitee gets to it, which is next week as often as not.
INVITE_TTL = timedelta(days=7)
```

and add `"INVITE_TTL"` to that module's `__all__`.

- [ ] **Step 4: The migration**

Create `backend/alembic/versions/0027_invitations.py`:

```python
"""invited_at on users; invitation kind on auth_token

Revision ID: 0027_invitations
Revises: 0026_auth_token
Create Date: 2026-09-03 19:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_invitations"
down_revision: str | None = "0026_auth_token"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Postgres refuses ALTER TYPE ... ADD VALUE inside a transaction, and
    # Alembic wraps every migration in one. autocommit_block exists for this.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE auth_token_kind ADD VALUE IF NOT EXISTS 'invitation'")


def downgrade() -> None:
    # Postgres cannot remove a value from an enum. Rows of the new kind must
    # go first, and the value stays in the type — a documented one-way door.
    op.execute("DELETE FROM auth_token WHERE kind = 'invitation'")
    op.drop_column("users", "invited_at")
```

- [ ] **Step 5: The two service functions**

In `backend/app/services/user.py`, add `import secrets` and
`from datetime import UTC, datetime` to the imports, then after `create_user`:

```python
async def invite_user(
    db: AsyncSession, *, email: str, full_name: str, role: UserRole
) -> User:
    """Create an inactive row that reserves the address until they accept.

    A real row, not a pending-invitation table: two admins inviting the same
    person, or an invite racing a direct create, collide on the existing unique
    email constraint rather than on new logic.

    The password is a genuine Argon2 hash of random bytes. Login verifies it
    like any other, so there is no timing shortcut announcing "this account has
    no real password". Raises :class:`EmailAlreadyExistsError`. Commits.
    """
    normalized = email.lower()
    if await get_by_email(db, normalized) is not None:
        raise EmailAlreadyExistsError(normalized)

    user = User(
        email=normalized,
        hashed_password=hash_password(secrets.token_urlsafe(32)),
        full_name=full_name,
        role=role,
        is_active=False,
        invited_at=datetime.now(UTC),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def accept_invitation(
    db: AsyncSession, user: User, *, full_name: str, password: str
) -> None:
    """Turn an invited row into a real account. Commits.

    Clears ``invited_at`` and activates in the same write, so there is never a
    row that is active and still invited.
    """
    user.full_name = full_name
    user.hashed_password = hash_password(password)
    user.invited_at = None
    user.is_active = True
    # No sessions exist yet, but "a password was set, therefore the version
    # moved" should hold everywhere a password is set.
    user.token_version += 1
    await db.commit()
```

Add `"accept_invitation"` and `"invite_user"` to that module's `__all__`.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
docker exec megoopm-test python -m pytest tests/test_invitations_service.py tests/test_auth_tokens.py -p no:cacheprovider -p no:warnings
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/user.py backend/app/models/enums.py \
        backend/alembic/versions/0027_invitations.py backend/app/services/auth_tokens.py \
        backend/app/services/user.py backend/tests/test_invitations_service.py
git commit -m "feat(users): invite and accept, in the service layer

An invited user is a real row from the moment the invitation is sent —
inactive, with a genuine Argon2 hash of random bytes, and invited_at set. The
address is reserved by the existing unique constraint, and every existing
screen and guard already knows what a user is.

The enum value is added in an autocommit block: Postgres refuses ALTER TYPE
ADD VALUE inside the transaction Alembic wraps every migration in.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: The invitation email

Names the app, names who sent it, carries the link, states the expiry. The
sender's name is not decoration: an invitation with no human attached is what
phishing looks like.

**Files:**
- Create: `backend/app/services/mail/templates/invitation.html.j2`, `.txt.j2`
- Test: `backend/tests/test_mail_templates.py` (append)

**Interfaces:**
- Consumes: `render` from `app.services.mail.templates`.
- Produces: template `invitation` with context `app_name`, `inviter_name`, `accept_url`, `ttl_days`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_mail_templates.py`:

```python
# --- invitation -----------------------------------------------------------


def _invitation(**over):
    context = {
        "app_name": "MegooPM",
        "inviter_name": "Mohamed Hammad",
        "accept_url": "https://pm.example.com/accept-invite?token=abc",
        "ttl_days": 7,
    }
    context.update(over)
    return render("invitation", subject="You're invited", **context)


def test_invitation_carries_the_link_in_both_bodies() -> None:
    email = _invitation()
    assert "https://pm.example.com/accept-invite?token=abc" in email.html
    assert "https://pm.example.com/accept-invite?token=abc" in email.text


def test_invitation_names_who_sent_it() -> None:
    # "You've been invited to MegooPM" with no human attached is what phishing
    # looks like.
    email = _invitation(inviter_name="Sara Ali")
    assert "Sara Ali" in email.html
    assert "Sara Ali" in email.text


def test_invitation_states_the_expiry_in_days() -> None:
    email = _invitation(ttl_days=7)
    assert "7 days" in email.text


def test_inviter_name_is_escaped_in_the_html_body() -> None:
    # The inviter is an admin, but an admin's display name is still user input.
    email = _invitation(inviter_name="<b>x</b>")
    assert "<b>x</b>" not in email.html
    assert "&lt;b&gt;" in email.html
```

- [ ] **Step 2: Run them to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_mail_templates.py -p no:cacheprovider -p no:warnings
```
Expected: 4 FAIL with `TemplateNotFound: 'invitation.html.j2'`.

- [ ] **Step 3: Write the templates**

Create `backend/app/services/mail/templates/invitation.html.j2`:

```jinja
{% extends "base.html.j2" %}
{% block subject_text %}You're invited to {{ app_name }}{% endblock %}
{% block body %}
<p class="m-accent" style="margin:0 0 16px 0;font-size:18px;font-weight:600;
                           color:{{ light.primary }};">You're invited to {{ app_name }}</p>
<p style="margin:0 0 20px 0;">
  {{ inviter_name }} has invited you to {{ app_name }}. Use the button below to
  choose your name and password. The link works once and expires in
  {{ ttl_days }} days.
</p>
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 20px 0;">
  <tr>
    <td class="m-btn" style="background:{{ light.primary }};border-radius:8px;">
      <a href="{{ accept_url }}"
         style="display:inline-block;padding:11px 20px;color:{{ light.primary_foreground }};
                text-decoration:none;font-weight:600;font-size:14px;">Accept invitation</a>
    </td>
  </tr>
</table>
<p class="m-muted" style="margin:0 0 8px 0;color:{{ light.muted_foreground }};font-size:13px;">
  If the button does not work, paste this into your browser:
</p>
<p class="m-muted" style="margin:0 0 20px 0;color:{{ light.muted_foreground }};font-size:12px;
                          word-break:break-all;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;">
  {{ accept_url }}
</p>
<p class="m-muted" style="margin:0;color:{{ light.muted_foreground }};font-size:13px;">
  If you were not expecting this, you can ignore it. No account is created
  until you accept.
</p>
{% endblock %}
```

Create `backend/app/services/mail/templates/invitation.txt.j2`:

```jinja
You're invited to {{ app_name }}

{{ inviter_name }} has invited you to {{ app_name }}. Open this link to choose
your name and password. It works once and expires in {{ ttl_days }} days.

{{ accept_url }}

If you were not expecting this, you can ignore it. No account is created
until you accept.

--
Sent by {{ app_name }}.
```

- [ ] **Step 4: Run them to verify they pass**

```bash
docker exec megoopm-test python -m pytest tests/test_mail_templates.py -p no:cacheprovider -p no:warnings
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/mail/templates/invitation.html.j2 \
        backend/app/services/mail/templates/invitation.txt.j2 backend/tests/test_mail_templates.py
git commit -m "feat(mail): the invitation email

Names who sent it. An invitation with no human attached is what phishing looks
like.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The three routes

Two admin routes on `/users`, one public route on `/auth`. The invitation is
sent from one helper both admin routes call, so the email is built in exactly
one place.

**Files:**
- Modify: `backend/app/schemas/user.py`
- Modify: `backend/app/schemas/auth.py`
- Modify: `backend/app/api/routes/users.py`
- Modify: `backend/app/api/routes/auth.py`
- Modify: `backend/openapi.json` (regenerated)
- Test: `backend/tests/test_invitations_api.py`

**Interfaces:**
- Consumes: Tasks 1 and 2; P2's `rate_limit`, `client_ip`, `auth_tokens`, `send_email_task`, `APP_NAME`, and `_limit` / `_unavailable` from `routes/auth.py`.
- Produces:
  - `UserInvite(email: EmailStr, full_name: str = "", role: UserRole = member)`
  - `UserRead.invited_at: datetime | None`
  - `AcceptInviteRequest(token: str, full_name: str, password: str)`
  - `POST /users/invite` → 201 `UserRead`; 409 taken / mail unconfigured
  - `POST /users/{id}/invite` → 204; 409 not invited / mail unconfigured
  - `POST /auth/accept-invite` → 204; 400 refused token; 429; 503

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_invitations_api.py`:

```python
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
            json={"email": email, "password": "password123", "role": "member",
                  "is_active": state == "active"},
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
    db_client: AsyncClient, admin_token: str, admin_user: User, mail_configured, mail, session_factory
) -> None:
    body = await _invite(db_client, admin_token, "new@example.com")
    async with session_factory() as db:
        rows = (
            await db.execute(
                select(AuditLog).where(AuditLog.object_type == "user", AuditLog.object_id == body["id"])
            )
        ).scalars().all()
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
    resp = await db_client.post(f"/api/v1/users/{admin_user.id}/invite", headers=_auth(admin_token))
    assert resp.status_code == 409
    assert mail.calls == []


async def test_resend_404s_for_an_unknown_user(
    db_client: AsyncClient, admin_token: str, mail_configured, mail
) -> None:
    resp = await db_client.post("/api/v1/users/999999/invite", headers=_auth(admin_token))
    assert resp.status_code == 404


# --- POST /auth/accept-invite -----------------------------------------------


async def _token_from(mail: RecordingTask) -> str:
    return mail.calls[-1]["context"]["accept_url"].split("token=")[1]


async def test_accept_activates_and_the_new_password_works(
    db_client: AsyncClient, admin_token: str, mail_configured, mail, fake_redis
) -> None:
    body = await _invite(db_client, admin_token, "new@example.com")
    raw = await _token_from(mail)

    resp = await db_client.post(
        ACCEPT, json={"token": raw, "full_name": "New Person", "password": "chosen12345"}
    )
    assert resp.status_code == 204, resp.text

    login = await db_client.post(LOGIN, json={"email": "new@example.com", "password": "chosen12345"})
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
    raw = await _token_from(mail)
    await db_client.post(ACCEPT, json={"token": raw, "full_name": "N", "password": "chosen12345"})

    again = await db_client.post(ACCEPT, json={"token": raw, "full_name": "N", "password": "other12345"})
    assert again.status_code == 400


async def test_accept_refuses_a_reset_token(
    db_client: AsyncClient, admin_token: str, mail_configured, mail, fake_redis, session_factory
) -> None:
    body = await _invite(db_client, admin_token, "new@example.com")
    async with session_factory() as db:
        user = await db.get(User, body["id"])
        raw = await issue(db, user=user, kind=AuthTokenKind.password_reset, ttl=INVITE_TTL)

    resp = await db_client.post(ACCEPT, json={"token": raw, "full_name": "N", "password": "chosen12345"})
    assert resp.status_code == 400


async def test_accept_is_rate_limited_per_ip(
    db_client: AsyncClient, mail_configured, fake_redis
) -> None:
    for _ in range(rate_limit.RESET_IP_LIMIT):
        resp = await db_client.post(ACCEPT, json={"token": "nope", "full_name": "N", "password": "x" * 12})
        assert resp.status_code == 400
    resp = await db_client.post(ACCEPT, json={"token": "nope", "full_name": "N", "password": "x" * 12})
    assert resp.status_code == 429


async def test_accept_writes_an_audit_row_with_the_new_user_as_actor(
    db_client: AsyncClient, admin_token: str, mail_configured, mail, fake_redis, session_factory
) -> None:
    body = await _invite(db_client, admin_token, "new@example.com")
    raw = await _token_from(mail)
    await db_client.post(ACCEPT, json={"token": raw, "full_name": "N", "password": "chosen12345"})

    async with session_factory() as db:
        rows = (
            await db.execute(
                select(AuditLog).where(AuditLog.object_type == "user", AuditLog.object_id == body["id"])
            )
        ).scalars().all()
    assert any(r.meta.get("invitation_accepted") and r.actor == "new@example.com" for r in rows)


async def test_a_short_password_is_refused_before_the_token_is_spent(
    db_client: AsyncClient, admin_token: str, mail_configured, mail, fake_redis
) -> None:
    await _invite(db_client, admin_token, "new@example.com")
    raw = await _token_from(mail)

    resp = await db_client.post(ACCEPT, json={"token": raw, "full_name": "N", "password": "short"})
    assert resp.status_code == 422

    resp = await db_client.post(ACCEPT, json={"token": raw, "full_name": "N", "password": "chosen12345"})
    assert resp.status_code == 204
```

- [ ] **Step 2: Run them to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_invitations_api.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — `AttributeError: … has no attribute 'send_email_task'` on
`users_routes`, and 404/405 on the routes.

- [ ] **Step 3: The schemas**

In `backend/app/schemas/user.py`, add after `UserCreate`:

```python
class UserInvite(BaseModel):
    """Payload to invite a user. No password: they choose one when they accept."""

    email: EmailStr
    full_name: str = Field(default="", max_length=255)
    role: UserRole = UserRole.member
```

and in `UserRead`, after `updated_at`:

```python
    # Set while an invitation is outstanding. The users table renders the
    # Invited badge and the resend action from this alone.
    invited_at: datetime | None = None
```

Add `"UserInvite"` to that module's `__all__`.

In `backend/app/schemas/auth.py`, after `ResetPasswordRequest`:

```python
class AcceptInviteRequest(BaseModel):
    """Body for ``POST /auth/accept-invite``."""

    token: str = Field(min_length=1, max_length=256)
    full_name: str = Field(default="", max_length=255)
    password: str = Field(min_length=8, max_length=128)
```

and add `"AcceptInviteRequest"` to `__all__`.

- [ ] **Step 4: The admin routes**

In `backend/app/api/routes/users.py`, extend the imports:

```python
from app.models.enums import AuditAction, AuthTokenKind
from app.schemas.user import (
    PasswordChange,
    PasswordReset,
    ProfileUpdate,
    UserCreate,
    UserInvite,
    UserRead,
    UserUpdate,
)
from app.services import auth_tokens
from app.services import instance_settings as settings_service
from app.services import user as user_service
from app.services.audit import record_audit
from app.services.mail.templates import APP_NAME
from app.tasks.mail import send_email as send_email_task
```

Add the helper after `_conflict`:

```python
async def _send_invitation(db: AsyncSession, user: User, *, inviter: User) -> None:
    """Issue a fresh invitation token and queue the email. One place, so the
    initial invite and a resend can never drift apart."""
    row = await settings_service.get_instance_settings(db)
    if not (row.smtp_enabled and row.app_url):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is not configured, so an invitation cannot be sent. "
            "Set up SMTP and the app URL in Settings first.",
        )
    raw = await auth_tokens.issue(
        db, user=user, kind=AuthTokenKind.invitation, ttl=auth_tokens.INVITE_TTL
    )
    send_email_task.delay(
        to=user.email,
        template="invitation",
        subject=f"You're invited to {APP_NAME}",
        context={
            "app_name": APP_NAME,
            "inviter_name": inviter.full_name.strip() or inviter.email,
            "accept_url": f"{row.app_url}/accept-invite?token={raw}",
            "ttl_days": auth_tokens.INVITE_TTL.days,
        },
    )
```

Then, **before** the `/{user_id}` routes (route order matters — `/invite` must
not be captured by the integer path parameter), add:

```python
@router.post("/invite", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def invite_user(body: UserInvite, admin: AdminUser, db: SessionDep) -> UserRead:
    """Create an invited user and send them the link. Admin-only.

    409 on a taken address in every state — active, inactive, or already
    invited. The fix for "they never got it" is resend, not a second invite.
    """
    # Check email before creating the row, so a misconfigured instance does
    # not accumulate invited users nobody can reach.
    row = await settings_service.get_instance_settings(db)
    if not (row.smtp_enabled and row.app_url):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is not configured, so an invitation cannot be sent. "
            "Set up SMTP and the app URL in Settings first.",
        )
    try:
        user = await user_service.invite_user(
            db, email=body.email, full_name=body.full_name, role=body.role
        )
    except user_service.EmailAlreadyExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with that email already exists",
        ) from None
    await _send_invitation(db, user, inviter=admin)
    await _audit(
        db,
        actor=admin,
        action=AuditAction.create,
        object_id=user.id,
        meta={"email": user.email, "role": user.role.value, "invited": True},
    )
    return UserRead.model_validate(user)
```

and, with the other `/{user_id}` routes:

```python
@router.post("/{user_id}/invite", status_code=status.HTTP_204_NO_CONTENT)
async def resend_invitation(user_id: int, admin: AdminUser, db: SessionDep) -> None:
    """Send a fresh invitation to a user who has not yet accepted. Admin-only.

    Refused for an accepted user: they have a password, and re-inviting them
    would hand anyone with their inbox a way to reset it.
    """
    user = await _get_or_404(db, user_id)
    if user.invited_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That user has already accepted their invitation.",
        )
    await _send_invitation(db, user, inviter=admin)
    await _audit(
        db,
        actor=admin,
        action=AuditAction.update,
        object_id=user.id,
        meta={"invitation_resent": True},
    )
```

- [ ] **Step 5: The accept route**

In `backend/app/api/routes/auth.py`, add `AcceptInviteRequest` to the schema
import, then after `reset_password`:

```python
@router.post("/accept-invite", status_code=status.HTTP_204_NO_CONTENT)
async def accept_invite(body: AcceptInviteRequest, request: Request, db: SessionDep) -> None:
    """Spend an invitation token: set the name and password, activate.

    Then the invitee goes to the login page rather than into a session, for
    the same reason as a reset: the token arrived by email.
    """
    try:
        await rate_limit.check_password_reset_redeem(ip=client_ip(request))
    except rate_limit.RateLimited as exc:
        raise _limit(exc) from None
    except rate_limit.RateLimitUnavailable:
        raise _unavailable() from None

    try:
        row = await auth_tokens.redeem(db, raw=body.token, kind=AuthTokenKind.invitation)
    except auth_tokens.TokenInvalid as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    user = await user_service.get_by_id(db, row.user_id)
    if user is None or user.invited_at is None:
        # Deleted (revoked) since the email went out, or already accepted.
        # Same message as every other refusal.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(auth_tokens.TokenInvalid())
        )

    await user_service.accept_invitation(db, user, full_name=body.full_name, password=body.password)
    await record_audit(
        db,
        actor=user.email,
        action=AuditAction.update,
        object_type="user",
        object_id=user.id,
        meta={"invitation_accepted": True},
    )
    await db.commit()
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
docker exec megoopm-test python -m pytest tests/test_invitations_api.py tests/test_users_management.py tests/test_users_rbac.py tests/test_auth.py -p no:cacheprovider -p no:warnings
```
Expected: PASS. The users suites are included because `UserRead` changed.

- [ ] **Step 7: Regenerate OpenAPI, lint, run everything**

```bash
docker exec megoopm-test python -m scripts.export_openapi
docker exec megoopm-test ruff check app tests
docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings
```
Expected: all green, including `test_openapi.py`.

- [ ] **Step 8: Commit**

```bash
git add backend/app/schemas/user.py backend/app/schemas/auth.py \
        backend/app/api/routes/users.py backend/app/api/routes/auth.py \
        backend/tests/test_invitations_api.py backend/openapi.json
git commit -m "feat(users): invite, resend, and accept

409 on a taken address in every state — the fix for 'they never got it' is
resend, not a second row that fails on the email constraint anyway. Resend is
refused for an accepted user: it would hand anyone with their inbox a way to
reset their password.

The invitation is built in one helper both admin routes call, so the initial
invite and a resend can never drift apart.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The accept page

The public side: the API calls, the route registration, the form, and the
fixture updates that `UserRead.invited_at` forces on every frontend test that
builds a `User`.

**Files:**
- Modify: `frontend/src/lib/api/generated/schema.ts` (regenerated)
- Modify: `frontend/src/lib/auth/api.ts`
- Modify: `frontend/src/lib/auth/session.ts`
- Modify: `frontend/src/lib/api/resources/users.ts`
- Create: `frontend/src/components/auth/accept-invite-form.tsx`
- Create: `frontend/src/app/accept-invite/page.tsx`
- Modify: four test fixtures (Step 2)
- Test: `frontend/src/lib/auth/session.test.ts` (append)
- Test: `frontend/src/components/auth/accept-invite-form.test.tsx`

**Interfaces:**
- Produces:
  - `acceptInvite(token: string, fullName: string, password: string): Promise<void>`
  - `ACCEPT_INVITE_ROUTE = "/accept-invite"` in `PUBLIC_ROUTES`
  - `users.invite(body: UserInvite): Promise<User>`, `users.resendInvite(id: number): Promise<void>`, `type UserInvite`

- [ ] **Step 1: Regenerate the types**

```bash
cd frontend && npm run gen:api && npm run typecheck
```
Expected: typecheck **fails** on four fixture files — `invited_at` is now
required on `User`. That is the point of running it now.

- [ ] **Step 2: Add `invited_at` to every `User` fixture**

Five lines across four files; each `is_active: <bool>,` in a `User` literal
gets `invited_at: null,` after it:

```bash
cd frontend && python - <<'PY'
import io, re
for p in ("src/components/app-topbar.test.tsx",
          "src/components/profile/profile-view.test.tsx",
          "src/lib/api/resources/users.test.ts",
          "src/components/users/users-view.test.tsx"):
    s = io.open(p, encoding="utf-8").read()
    new, n = re.subn(r"( *)is_active: (true|false),\n", lambda m: m.group(0) + f"{m.group(1)}invited_at: null,\n", s)
    io.open(p, "w", encoding="utf-8", newline="\n").write(new)
    print(p, n)
PY
npm run typecheck
```
Expected: 1, 1, 1, 2 — and typecheck passes.

- [ ] **Step 3: Write the failing tests**

Append to `frontend/src/lib/auth/session.test.ts`:

```ts
describe("public route for accepting an invitation", () => {
  it("lets the invitee reach the accept page", () => {
    expect(isPublicRoute("/accept-invite")).toBe(true);
  });
});
```

Create `frontend/src/components/auth/accept-invite-form.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

let token: string | null = "inv-123";
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(token ? { token } : {}),
}));
vi.mock("@/lib/auth/api", () => ({ acceptInvite: vi.fn() }));

import { ApiError } from "@/lib/api/errors";
import { acceptInvite } from "@/lib/auth/api";
import { AcceptInviteForm } from "@/components/auth/accept-invite-form";

afterEach(() => {
  cleanup();
  vi.mocked(acceptInvite).mockReset();
  token = "inv-123";
});

async function fill(user: ReturnType<typeof userEvent.setup>, confirm = "chosen12345") {
  await user.type(screen.getByLabelText("Full name"), "New Person");
  await user.type(screen.getByLabelText("Password"), "chosen12345");
  await user.type(screen.getByLabelText("Confirm password"), confirm);
}

describe("AcceptInviteForm", () => {
  it("sends the token, name and password", async () => {
    const user = userEvent.setup();
    vi.mocked(acceptInvite).mockResolvedValue(undefined);
    render(<AcceptInviteForm />);

    await fill(user);
    await user.click(screen.getByRole("button", { name: /accept invitation/i }));

    expect(acceptInvite).toHaveBeenCalledWith("inv-123", "New Person", "chosen12345");
    expect(await screen.findByText(/account is ready/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /sign in/i })).toHaveAttribute("href", "/login");
  });

  it("refuses mismatched passwords before sending anything", async () => {
    const user = userEvent.setup();
    render(<AcceptInviteForm />);

    await fill(user, "different12345");
    await user.click(screen.getByRole("button", { name: /accept invitation/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/match/i);
    expect(acceptInvite).not.toHaveBeenCalled();
  });

  it("points a refused token at an administrator, not a resend", async () => {
    // There is no self-service resend: the only address to send to is the
    // one the person holding the link already controls.
    const user = userEvent.setup();
    vi.mocked(acceptInvite).mockRejectedValue(
      new ApiError(400, "Bad request", { detail: "This link is invalid or has expired." }),
    );
    render(<AcceptInviteForm />);

    await fill(user);
    await user.click(screen.getByRole("button", { name: /accept invitation/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/administrator/i);
    expect(screen.queryByRole("link", { name: /request a new/i })).not.toBeInTheDocument();
  });

  it("says the link is incomplete when there is no token", () => {
    token = null;
    render(<AcceptInviteForm />);
    expect(screen.getByText(/link is incomplete/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 4: Run them to verify they fail**

```bash
cd frontend && npx vitest run src/lib/auth/session.test.ts src/components/auth/accept-invite-form.test.tsx
```
Expected: the route test fails (`false`); the form file fails to resolve.

- [ ] **Step 5: The API calls and the route**

In `frontend/src/lib/auth/session.ts`:

```ts
/** Route the invitation email lands on. */
export const ACCEPT_INVITE_ROUTE = "/accept-invite";

/** Routes that never require a session (login, health, static handled by matcher). */
export const PUBLIC_ROUTES: readonly string[] = [
  LOGIN_ROUTE,
  FORGOT_PASSWORD_ROUTE,
  RESET_PASSWORD_ROUTE,
  ACCEPT_INVITE_ROUTE,
];
```

Append to `frontend/src/lib/auth/api.ts`:

```ts
/** Spend an invitation token. Refused tokens are a 400 with one message. */
export function acceptInvite(token: string, fullName: string, password: string): Promise<void> {
  return apiFetch<void>("/api/v1/auth/accept-invite", {
    method: "POST",
    body: { token, full_name: fullName, password },
    token: null,
  });
}
```

In `frontend/src/lib/api/resources/users.ts`, add the type and two calls:

```ts
export type UserInvite = Schemas["UserInvite"];
```

```ts
  /** Create an invited (inactive) user and email them the link. 409 if taken or mail is off. */
  invite: (body: UserInvite) => api.post<User>(`${BASE}/invite`, body),
  /** A fresh link for a user who has not accepted yet. 409 once they have. */
  resendInvite: (id: number) => api.post<void>(`${BASE}/${id}/invite`, {}),
```

Re-export `UserInvite` from `frontend/src/lib/api/index.ts` beside the other
user types.

- [ ] **Step 6: The form and the page**

Create `frontend/src/components/auth/accept-invite-form.tsx`:

```tsx
"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";

import { APP_NAME } from "@/lib/env";
import { ApiError } from "@/lib/api/errors";
import { acceptInvite } from "@/lib/auth/api";
import { LOGIN_ROUTE } from "@/lib/auth/session";
import { validateNewPassword } from "@/components/users/lib";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/**
 * The page the invitation email lands on.
 *
 * A refused token points at an administrator, not at a resend: the only
 * address an invitation could be resent to is the one the person holding the
 * link already controls.
 */
export function AcceptInviteForm() {
  const token = useSearchParams().get("token");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;
    const problem = validateNewPassword(password, confirm);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await acceptInvite(token, fullName.trim(), password);
      setDone(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) {
        setError(`${err.detail} Ask an administrator to send you a new invitation.`);
      } else if (err instanceof ApiError && err.status === 429) {
        setError("Too many attempts. Please wait a while and try again.");
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center p-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="space-y-1 text-center">
          <h1 className="text-xl font-semibold">Join {APP_NAME}</h1>
          <p className="text-muted-foreground text-sm">Choose your name and a password.</p>
        </div>

        {!token ? (
          <p className="rounded-lg border p-4 text-sm">
            This link is incomplete. Open the link from your email again, or ask an
            administrator to send a new invitation.
          </p>
        ) : done ? (
          <div className="space-y-3 rounded-lg border p-4 text-sm">
            <p>Your account is ready.</p>
            <Link href={LOGIN_ROUTE} className="text-primary underline-offset-4 hover:underline">
              Sign in
            </Link>
          </div>
        ) : (
          <form className="space-y-3" onSubmit={onSubmit} noValidate>
            <Input
              name="full-name"
              placeholder="Full name"
              autoComplete="name"
              aria-label="Full name"
              autoFocus
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              disabled={submitting}
            />
            <Input
              type="password"
              name="new-password"
              placeholder="Password"
              autoComplete="new-password"
              aria-label="Password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={submitting}
            />
            <Input
              type="password"
              name="confirm-password"
              placeholder="Confirm password"
              autoComplete="new-password"
              aria-label="Confirm password"
              required
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              disabled={submitting}
            />
            {error ? (
              <p role="alert" className="text-destructive text-sm">
                {error}
              </p>
            ) : null}
            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? "Saving…" : "Accept invitation"}
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
```

Create `frontend/src/app/accept-invite/page.tsx`:

```tsx
import type { Metadata } from "next";
import { Suspense } from "react";

import { AcceptInviteForm } from "@/components/auth/accept-invite-form";

export const metadata: Metadata = { title: "Accept invitation" };

/** Suspense because the form reads the token from the query string. */
export default function AcceptInvitePage() {
  return (
    <Suspense>
      <AcceptInviteForm />
    </Suspense>
  );
}
```

- [ ] **Step 7: Run them to verify they pass, then typecheck and lint**

```bash
cd frontend && npx vitest run src/lib/auth/session.test.ts src/components/auth
cd frontend && npm run typecheck && npm run lint
```
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib frontend/src/components/auth frontend/src/app/accept-invite \
        frontend/src/components/app-topbar.test.tsx frontend/src/components/profile/profile-view.test.tsx \
        frontend/src/components/users/users-view.test.tsx
git commit -m "feat(login): the accept-invitation page

A refused token points at an administrator, not at a resend: the only address
an invitation could be resent to is the one the person holding the link
already controls.

UserRead gained invited_at, so four existing User fixtures gained it too —
vitest passed throughout; typecheck is what caught it.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: The admin surface

An Invite button that appears only when an invitation could be sent, a dialog,
the Invited badge, and a resend action on invited rows.

**Files:**
- Create: `frontend/src/components/users/invite-dialog.tsx`
- Modify: `frontend/src/components/users/users-view.tsx`
- Test: `frontend/src/components/users/invite-dialog.test.tsx`
- Test: `frontend/src/components/users/users-view.test.tsx` (append)

**Interfaces:**
- Consumes: `users.invite`, `users.resendInvite`, `fetchCapabilities`.
- Produces: `InviteDialog({ open, onOpenChange, onSaved })`.

- [ ] **Step 1: Write the failing dialog tests**

Create `frontend/src/components/users/invite-dialog.test.tsx`:

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { users } from "@/lib/api";
import { ApiError } from "@/lib/api/errors";
import { InviteDialog } from "@/components/users/invite-dialog";

const INVITED = {
  id: 9,
  email: "new@example.com",
  full_name: "",
  role: "member" as const,
  is_active: false,
  invited_at: "2026-09-03T00:00:00Z",
  created_at: "2026-09-03T00:00:00Z",
  updated_at: "2026-09-03T00:00:00Z",
};

beforeEach(() => {
  vi.spyOn(toast, "success").mockImplementation(() => "" as never);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("InviteDialog", () => {
  it("sends email, name and role, then reports back", async () => {
    const user = userEvent.setup();
    const invite = vi.spyOn(users, "invite").mockResolvedValue(INVITED);
    const onSaved = vi.fn();
    render(<InviteDialog open onOpenChange={() => {}} onSaved={onSaved} />);

    await user.type(screen.getByLabelText("Email"), "new@example.com");
    await user.click(screen.getByRole("button", { name: /send invitation/i }));

    await waitFor(() => expect(invite).toHaveBeenCalled());
    expect(invite.mock.calls[0][0]).toMatchObject({ email: "new@example.com", role: "member" });
    expect(onSaved).toHaveBeenCalled();
  });

  it("refuses an empty email before sending", async () => {
    const user = userEvent.setup();
    const invite = vi.spyOn(users, "invite");
    render(<InviteDialog open onOpenChange={() => {}} onSaved={() => {}} />);

    await user.click(screen.getByRole("button", { name: /send invitation/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/email/i);
    expect(invite).not.toHaveBeenCalled();
  });

  it("surfaces the backend's reason for a refusal", async () => {
    const user = userEvent.setup();
    vi.spyOn(users, "invite").mockRejectedValue(
      new ApiError(409, "Conflict", { detail: "A user with that email already exists" }),
    );
    render(<InviteDialog open onOpenChange={() => {}} onSaved={() => {}} />);

    await user.type(screen.getByLabelText("Email"), "taken@example.com");
    await user.click(screen.getByRole("button", { name: /send invitation/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/already exists/i);
  });
});
```

- [ ] **Step 2: Write the failing view tests**

Append to `frontend/src/components/users/users-view.test.tsx`. The file
already mocks `@/lib/auth/context`, `user-dialog`, and `reset-password-dialog`;
add two more mocks **beside those, before the component import**:

```tsx
vi.mock("@/lib/auth/api", () => ({ fetchCapabilities: vi.fn() }));
vi.mock("@/components/users/invite-dialog", () => ({
  InviteDialog: ({ open }: { open: boolean }) => (open ? <div>invite-dialog</div> : null),
}));
```

and `import { fetchCapabilities } from "@/lib/auth/api";` below the imports.
Then append:

```tsx
describe("UsersView invitations", () => {
  const invited = {
    ...member,
    id: 3,
    email: "pending@example.com",
    full_name: "",
    is_active: false,
    invited_at: "2026-09-03T00:00:00Z",
  };

  beforeEach(() => {
    vi.mocked(fetchCapabilities).mockResolvedValue({ password_reset: true });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("offers Invite user when an invitation could be sent", async () => {
    vi.spyOn(users, "list").mockResolvedValue([admin]);
    render(<UsersView />);
    expect(await screen.findByRole("button", { name: /invite user/i })).toBeInTheDocument();
  });

  it("hides Invite user when email is not configured", async () => {
    // An admin who can see Invite and then learns nothing can be sent has been
    // misled by the UI.
    vi.mocked(fetchCapabilities).mockResolvedValue({ password_reset: false });
    vi.spyOn(users, "list").mockResolvedValue([admin]);
    render(<UsersView />);
    await screen.findByText("admin@example.com");
    expect(screen.queryByRole("button", { name: /invite user/i })).not.toBeInTheDocument();
  });

  it("shows an Invited badge and a resend action on an invited row", async () => {
    vi.spyOn(users, "list").mockResolvedValue([admin, invited]);
    render(<UsersView />);
    const row = (await screen.findByText("pending@example.com")).closest("tr")!;
    expect(within(row).getByText("Invited")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: /resend invitation/i })).toBeInTheDocument();
  });

  it("shows neither on an accepted row", async () => {
    vi.spyOn(users, "list").mockResolvedValue([admin, member]);
    render(<UsersView />);
    const row = (await screen.findByText("member@example.com")).closest("tr")!;
    expect(within(row).queryByText("Invited")).not.toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: /resend invitation/i })).not.toBeInTheDocument();
  });

  it("resend calls the right route", async () => {
    const user = userEvent.setup();
    vi.spyOn(users, "list").mockResolvedValue([admin, invited]);
    const resend = vi.spyOn(users, "resendInvite").mockResolvedValue(undefined);
    render(<UsersView />);
    const row = (await screen.findByText("pending@example.com")).closest("tr")!;

    await user.click(within(row).getByRole("button", { name: /resend invitation/i }));

    await waitFor(() => expect(resend).toHaveBeenCalledWith(3));
  });

  it("calls the delete a withdrawal for an invited row", async () => {
    const user = userEvent.setup();
    vi.spyOn(users, "list").mockResolvedValue([admin, invited]);
    render(<UsersView />);
    const row = (await screen.findByText("pending@example.com")).closest("tr")!;

    await user.click(within(row).getByRole("button", { name: /delete pending@example.com/i }));

    expect(await screen.findByText(/withdraw/i)).toBeInTheDocument();
  });
});
```

The file's existing top-level tests now render with `fetchCapabilities` mocked
but unset; add to the **existing** top-level `beforeEach`:

```ts
    vi.mocked(fetchCapabilities).mockResolvedValue({ password_reset: false });
```

- [ ] **Step 3: Run both to verify they fail**

```bash
cd frontend && npx vitest run src/components/users
```
Expected: the dialog file fails to resolve; the view tests fail on the missing
button, badge and action.

- [ ] **Step 4: The dialog**

Create `frontend/src/components/users/invite-dialog.tsx`:

```tsx
"use client";

import { useState } from "react";
import { toast } from "sonner";

import { USER_ROLES, USER_ROLE_LABELS, users, type UserRole } from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/**
 * Invite someone by email. No password field: they choose one when they
 * accept. The parent remounts this with a `key` so the form starts fresh.
 */
export function InviteDialog({
  open,
  onOpenChange,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [role, setRole] = useState<UserRole>("member");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit() {
    setError(null);
    if (!email.trim()) return setError("Enter an email address.");
    setSaving(true);
    try {
      await users.invite({ email: email.trim(), full_name: fullName.trim(), role });
      toast.success("Invitation sent");
      onOpenChange(false);
      onSaved();
    } catch (err) {
      setError(describeError(err).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Invite user</DialogTitle>
          <DialogDescription>
            They&apos;ll get an email with a link to choose their name and password.
            The link expires in 7 days.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="invite-email">Email</Label>
            <Input
              id="invite-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="person@example.com"
              disabled={saving}
              autoFocus
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="invite-name">Full name</Label>
            <Input
              id="invite-name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Optional — they can set it when they accept"
              disabled={saving}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="invite-role">Role</Label>
            <Select value={role} onValueChange={(v) => setRole(v as UserRole)}>
              <SelectTrigger id="invite-role" disabled={saving}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {USER_ROLES.map((r) => (
                  <SelectItem key={r} value={r}>
                    {USER_ROLE_LABELS[r]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {error ? (
            <p role="alert" className="text-destructive text-sm">
              {error}
            </p>
          ) : null}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={saving}>
            {saving ? "Sending…" : "Send invitation"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 5: The view**

In `frontend/src/components/users/users-view.tsx`:

Imports — add `MailPlus`, `Send` to the lucide import; add
`import { fetchCapabilities } from "@/lib/auth/api";`,
`import { InviteDialog } from "@/components/users/invite-dialog";`, and
`import { toast } from "sonner";`.

Replace `StatusBadge`:

```tsx
function StatusBadge({ user }: { user: User }) {
  // Invited is a third state derived from one column: invited_at IS NOT NULL.
  if (user.invited_at) {
    return (
      <Badge variant="outline">
        <span className="size-1.5 rounded-full bg-primary" aria-hidden />
        Invited
      </Badge>
    );
  }
  return (
    <Badge variant={user.is_active ? "success" : "muted"}>
      <span
        className={`size-1.5 rounded-full ${user.is_active ? "bg-success" : "bg-muted-foreground"}`}
        aria-hidden
      />
      {user.is_active ? "Active" : "Inactive"}
    </Badge>
  );
}
```

and its call site `<StatusBadge active={u.is_active} />` → `<StatusBadge user={u} />`.

State — beside `deleteTarget`:

```tsx
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteKey, setInviteKey] = useState(0);
  // Hidden until the backend says an invitation could actually be sent.
  const [canInvite, setCanInvite] = useState(false);
```

Effect — after the existing load effect:

```tsx
  useEffect(() => {
    let active = true;
    fetchCapabilities()
      .then((caps) => {
        if (active) setCanInvite(caps.password_reset);
      })
      .catch(() => {
        // Leave the button hidden; the list load reports the real error.
      });
    return () => {
      active = false;
    };
  }, []);
```

Handler — beside `refresh`:

```tsx
  async function resend(u: User) {
    try {
      await users.resendInvite(u.id);
      toast.success(`Invitation resent to ${u.email}`);
    } catch (err) {
      toast.error(describeError(err).message);
    }
  }
```

Toolbar — replace the `flex justify-end` block:

```tsx
        <div className="flex justify-end gap-2">
          {canInvite ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setInviteKey((k) => k + 1);
                setInviteOpen(true);
              }}
            >
              <MailPlus /> Invite user
            </Button>
          ) : null}
          <Button size="sm" onClick={() => setUserDialog({ open: true, user: null })}>
            <Plus /> New user
          </Button>
        </div>
```

Actions — before the Edit button in the actions cell:

```tsx
                          {u.invited_at ? (
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              aria-label={`Resend invitation to ${u.email}`}
                              onClick={() => void resend(u)}
                            >
                              <Send />
                            </Button>
                          ) : null}
```

Delete copy — replace the `description` on `ConfirmDeleteDialog`:

```tsx
        title={deleteTarget?.invited_at ? "Withdraw invitation" : "Delete user"}
        description={
          deleteTarget
            ? deleteTarget.invited_at
              ? `Withdraw the invitation to ${deleteTarget.email}? Their link will stop working. You can invite them again later.`
              : `Delete ${displayName(deleteTarget)} (${deleteTarget.email})? They will be signed out immediately. This cannot be undone.`
            : ""
        }
```

Dialog — after `UserDialog`:

```tsx
      <InviteDialog
        key={`invite-${inviteKey}`}
        open={inviteOpen}
        onOpenChange={setInviteOpen}
        onSaved={refresh}
      />
```

- [ ] **Step 6: Run everything**

```bash
cd frontend && npx vitest run src/components/users && npm run typecheck && npm run lint && npm test
```
Expected: all green.

- [ ] **Step 7: Commit and tear down**

```bash
git add frontend/src/components/users
git commit -m "feat(users): invite from the Users page

The Invite button appears only when an invitation could be sent — an admin who
can see it and then learns nothing can be sent has been misled by the UI.

Invited is a third badge state derived from one column. Delete is the revoke,
and its confirmation says so for an invited row.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

```bash
docker rm -f megoopm-test megoopm-testdb && docker network rm megoopm-testnet
```

---

## Manual verification

With the stack up and SMTP configured:

- [ ] Invite an address you can read. The row appears as **Invited**; the
      email names you as the inviter; the link opens the accept page.
- [ ] Accept. The row turns **Active** with the name you chose; sign in works.
- [ ] Open the link again. It is refused, and the page points at an
      administrator with no resend link.
- [ ] Invite, then **Resend**. Only the second link works.
- [ ] Invite, then **Delete** the row. The confirmation says *withdraw*; the
      link is refused afterwards.
- [ ] Switch SMTP off. **Invite user** disappears from the Users page;
      **New user** stays.
- [ ] Invite an address that already has an account. The dialog shows the 409
      reason inline.
