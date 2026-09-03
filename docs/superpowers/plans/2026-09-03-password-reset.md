# Password Reset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A user who has forgotten their password requests a link by email, follows it, sets a new password, and every session they had open is ended.

**Architecture:** A hashed, single-use `auth_token` row (not a JWT) carries the reset; a `token_version` column on `users`, embedded as a `tv` claim and checked on refresh, ends existing sessions on any password change. Two unauthenticated routes are rate-limited in Redis with a client-IP rule that works both behind nginx and on the directly-published port. Emails go through the first real Celery task, which P1 deferred.

**Tech Stack:** FastAPI · SQLAlchemy 2.0 async · Alembic · Pydantic v2 · PyJWT · `redis.asyncio` · Celery · Jinja2 (P1's mailer) — backend. Next.js 16 / React 19 / base-ui / vitest — frontend. **No new dependencies.**

**Spec:** `docs/superpowers/specs/2026-09-03-password-reset-design.md`

## Global Constraints

- **No new packages.** Everything here is stdlib, or a dependency already present.
- **The raw token is never stored.** Only `sha256(token)` reaches the database.
- **`forgot-password` returns the identical status and body** for a registered address, an unknown one, and an inactive account.
- **A refused token gets one message** for absent, expired, and used. Distinguishing them tells an attacker which guesses were once valid.
- **The rate limiter fails closed** — Redis unreachable is 503, never "allow".
- **`token_version` is bumped by every password path** — the emailed reset, `PUT /users/me/password`, `PUT /users/{id}/password` — and by deactivation.
- **The mail task goes in `TASK_MODULES`**, and a test proves it is registered. The existing guard checks only `beat_schedule` and would not catch this one.
- **Both new pages go in `PUBLIC_ROUTES`**, or the route guard bounces them to `/login`.
- **Backend tests cannot run natively on Windows** (`fcntl`). Use the container recipe in Task 1, Step 2.
- Frontend commands run from `frontend/`: `npm test`, `npm run typecheck`, `npm run lint`.

## File Structure

**Backend**

| file | responsibility |
| --- | --- |
| `app/models/user.py` | `token_version` column |
| `app/core/security.py` | `tv` claim on both token types |
| `app/api/routes/auth.py` | `tv` check on refresh; the three new routes |
| `app/services/user.py` | bump on `set_password`, `change_own_password`, deactivation |
| `alembic/versions/0025_token_version.py` | migration |
| `app/models/enums.py` | `AuthTokenKind` |
| `app/models/auth_token.py` | the token row |
| `app/services/auth_tokens.py` | issue / redeem / supersede, hashing |
| `alembic/versions/0026_auth_token.py` | migration |
| `app/core/client_ip.py` | the trusted-proxy rule, one function |
| `app/services/rate_limit.py` | Redis counters with an injectable client |
| `app/tasks/mail.py` | the Celery send task |
| `app/services/mail/templates/password_reset.{html,txt}.j2` | the link email |
| `app/services/mail/templates/password_changed.{html,txt}.j2` | the notice |
| `app/schemas/auth.py` | three request/response models |
| `tests/conftest.py` | `AuthToken` and `InstanceSettings` in the SQLite table list |

**Frontend**

| file | responsibility |
| --- | --- |
| `src/lib/auth/api.ts` | `fetchCapabilities`, `requestPasswordReset`, `resetPassword` |
| `src/lib/auth/session.ts` | `PUBLIC_ROUTES` |
| `src/components/auth/forgot-password-form.tsx` | the email form + neutral message |
| `src/components/auth/reset-password-form.tsx` | token from query, two password fields |
| `src/app/forgot-password/page.tsx`, `src/app/reset-password/page.tsx` | routes |
| `src/components/login-form.tsx` | the link, gated on capabilities |

---

### Task 1: `token_version` ends sessions

A password reset is what someone does when they think they are compromised;
today the attacker's session survives it for seven days. This task closes that
for every password path, including the two already shipped.

**Files:**
- Modify: `backend/app/models/user.py`
- Create: `backend/alembic/versions/0025_token_version.py`
- Modify: `backend/app/core/security.py`
- Modify: `backend/app/api/routes/auth.py`
- Modify: `backend/app/services/user.py`
- Modify: `backend/tests/test_security.py`
- Test: `backend/tests/test_auth.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `User.token_version: int`
  - `create_access_token(subject, role, *, token_version: int)` and
    `create_refresh_token(subject, *, token_version: int)` — both now require it.
  - `user_service.bump_token_version(db, user) -> None` — commits.
  - `set_password`, `change_own_password` and a deactivating `update_user` all bump.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_auth.py`:

```python
# --- token_version: a password change ends existing sessions ---------------


async def _login(db_client: AsyncClient, email: str, password: str) -> dict:
    resp = await db_client.post(LOGIN, json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_refresh_is_refused_after_the_password_changes(
    db_client: AsyncClient, admin_user: User, session_factory
) -> None:
    # The scenario this exists for: someone resets their password because they
    # believe they are compromised. The attacker's refresh token must die.
    tokens = await _login(db_client, admin_user.email, "adminpass123")

    from app.services import user as user_service

    async with session_factory() as session:
        user = await user_service.get_by_id(session, admin_user.id)
        await user_service.set_password(session, user, "newpass12345")

    resp = await db_client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401


async def test_refresh_still_works_when_nothing_changed(
    db_client: AsyncClient, admin_user: User
) -> None:
    tokens = await _login(db_client, admin_user.email, "adminpass123")
    resp = await db_client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 200, resp.text


async def test_self_service_change_ends_other_sessions(
    db_client: AsyncClient, admin_user: User
) -> None:
    first = await _login(db_client, admin_user.email, "adminpass123")
    second = await _login(db_client, admin_user.email, "adminpass123")

    resp = await db_client.put(
        "/api/v1/users/me/password",
        headers={"Authorization": f"Bearer {second['access_token']}"},
        json={"new_password": "newpass12345"},
    )
    assert resp.status_code == 204, resp.text

    # The *other* session's refresh is dead.
    resp = await db_client.post(REFRESH, json={"refresh_token": first["refresh_token"]})
    assert resp.status_code == 401


async def test_admin_reset_ends_the_target_users_sessions(
    db_client: AsyncClient, admin_user: User, member_user: User, admin_token: str
) -> None:
    member = await _login(db_client, member_user.email, "memberpass123")

    resp = await db_client.put(
        f"/api/v1/users/{member_user.id}/password",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"password": "newpass12345"},
    )
    assert resp.status_code == 204, resp.text

    resp = await db_client.post(REFRESH, json={"refresh_token": member["refresh_token"]})
    assert resp.status_code == 401


async def test_deactivation_ends_sessions(
    db_client: AsyncClient, admin_user: User, member_user: User, admin_token: str
) -> None:
    # Same hole, same fix: is_active=false used to leave the session running
    # until the refresh token expired on its own.
    member = await _login(db_client, member_user.email, "memberpass123")

    resp = await db_client.patch(
        f"/api/v1/users/{member_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"is_active": False},
    )
    assert resp.status_code == 200, resp.text

    resp = await db_client.post(REFRESH, json={"refresh_token": member["refresh_token"]})
    assert resp.status_code == 401


async def test_access_token_is_not_checked_against_the_version(
    db_client: AsyncClient, admin_user: User, session_factory
) -> None:
    # Deliberate: a database read on every authenticated request is not worth
    # it for a token that lives minutes. The spec records this as a known limit.
    tokens = await _login(db_client, admin_user.email, "adminpass123")

    from app.services import user as user_service

    async with session_factory() as session:
        user = await user_service.get_by_id(session, admin_user.id)
        await user_service.set_password(session, user, "newpass12345")

    resp = await db_client.get(ME, headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert resp.status_code == 200
```

Check the top of `tests/test_auth.py` imports `User` and the three URL constants; they are already there. `member_user` and `member_token` exist in `conftest.py`.

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
docker exec megoopm-test python -m pytest tests/test_auth.py -p no:cacheprovider -p no:warnings
```

Expected: the five new tests FAIL — refresh returns 200 where 401 is expected.
(`test_access_token_is_not_checked…` passes already; it is a guard against a
later over-correction.) Run pytest **without** `-q`: `pyproject.toml` sets it,
and `-qq` swallows the summary.

- [ ] **Step 3: Add the column**

In `backend/app/models/user.py`, add `Integer` to the `sqlalchemy` import and,
after `is_active`:

```python
    # Bumped on every password change and on deactivation. Both token types
    # carry the value at issue; refresh refuses a mismatch, so a reset ends
    # every session the user had open instead of leaving them for seven days.
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
```

- [ ] **Step 4: Write the migration**

Create `backend/alembic/versions/0025_token_version.py`:

```python
"""token_version on users

An integer both JWT types carry and refresh checks. Bumping it ends every
session for that user — the missing half of "I reset my password because I
think I was compromised".

Revision ID: 0025_token_version
Revises: 0024_smtp_settings
Create Date: 2026-09-03 17:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_token_version"
down_revision: str | None = "0024_smtp_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
```

- [ ] **Step 5: Put the claim in both tokens**

In `backend/app/core/security.py`, replace the two constructors:

```python
def create_access_token(subject: str | int, role: str, *, token_version: int) -> str:
    """Issue a short-lived access token carrying the user's ``role``.

    ``tv`` is the user's token_version at issue. Access tokens are not checked
    against it — they live minutes — but carrying it keeps both token types the
    same shape, and a future check costs no re-issue.
    """
    return _create_token(
        subject=str(subject),
        token_type="access",
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
        extra_claims={"role": role, "tv": token_version},
    )


def create_refresh_token(subject: str | int, *, token_version: int) -> str:
    """Issue a longer-lived refresh token (no role claim; role is re-read on use).

    ``tv`` is what lets a password change end this session: refresh refuses a
    token whose version no longer matches the user's.
    """
    return _create_token(
        subject=str(subject),
        token_type="refresh",
        expires_delta=timedelta(minutes=settings.refresh_token_expire_minutes),
        extra_claims={"tv": token_version},
    )
```

- [ ] **Step 6: Check it on refresh**

In `backend/app/api/routes/auth.py`, replace `_issue_tokens` and the tail of
`refresh`:

```python
def _issue_tokens(user: User) -> TokenPair:
    return TokenPair(
        access_token=create_access_token(user.id, user.role.value, token_version=user.token_version),
        refresh_token=create_refresh_token(user.id, token_version=user.token_version),
    )
```

```python
    user = await user_service.get_by_id(db, user_id)
    if user is None or not user.is_active:
        raise invalid
    # A password change bumps the user's version; a refresh token minted
    # before it carries the old one. Refusing here is what makes "reset my
    # password" also mean "end the sessions I did not start".
    if payload.get("tv") != user.token_version:
        raise invalid
    return _issue_tokens(user)
```

Update the two call sites (`login` and `refresh`) from `_issue_tokens(user.id,
user.role.value)` to `_issue_tokens(user)`, and add
`from app.models.user import User` to the imports.

- [ ] **Step 7: Bump on every password path**

In `backend/app/services/user.py`, add before `set_password`:

```python
async def bump_token_version(db: AsyncSession, user: User) -> None:
    """End every session ``user`` has open. Commits.

    Refresh refuses a token whose ``tv`` claim no longer matches. Called from
    every path that changes a password, and from deactivation — three ways to
    change a password where one ends sessions and two do not is a rule nobody
    would remember.
    """
    user.token_version += 1
    await db.commit()
```

Then change `set_password` and `change_own_password` to bump before their
commit:

```python
async def set_password(db: AsyncSession, user: User, password: str) -> None:
    """Replace ``user``'s password (admin reset — no current-password check)."""
    user.hashed_password = hash_password(password)
    user.token_version += 1
    await db.commit()


async def change_own_password(db: AsyncSession, user: User, *, new_password: str) -> None:
    """Self-service change. No current-password check by design: holding a
    valid session for ``user`` is the only proof required."""
    user.hashed_password = hash_password(new_password)
    user.token_version += 1
    await db.commit()
```

And in `update_user`, inside the `is_active` branch:

```python
    if is_active is not None and is_active != user.is_active:
        changes["is_active"] = [user.is_active, is_active]
        user.is_active = is_active
        # Deactivation must end the sessions too, or the account is "off" in
        # the list and still signed in everywhere for up to seven days.
        if not is_active:
            user.token_version += 1
```

Add `"bump_token_version"` to that module's `__all__`.

- [ ] **Step 8: Update the four direct callers in `test_security.py`**

`token_version` is a required keyword on purpose — a caller that forgets it
should fail loudly, not silently mint a token that dies on first refresh. Four
existing tests call the constructors directly; give each `token_version=0`:

```python
    token = create_access_token(42, "admin", token_version=0)
```
```python
    token = create_refresh_token(7, token_version=0)
```
```python
    access = create_access_token(1, "member", token_version=0)
```
```python
    token = create_access_token(1, "member", token_version=0)
```

(Lines 33, 41, 48 and 54 of `backend/tests/test_security.py`.)

- [ ] **Step 9: Run the tests to verify they pass**

```bash
docker exec megoopm-test python -m pytest tests/test_auth.py tests/test_security.py tests/test_users_management.py tests/test_users_rbac.py -p no:cacheprovider -p no:warnings
```
Expected: PASS. The users suites are included because they exercise the
password routes this task changed.

- [ ] **Step 10: Commit**

```bash
git add backend/app/models/user.py backend/alembic/versions/0025_token_version.py \
        backend/app/core/security.py backend/app/api/routes/auth.py \
        backend/app/services/user.py backend/tests/test_auth.py backend/tests/test_security.py
git commit -m "feat(auth): a password change ends every open session

A reset is what someone does when they think they are compromised; until now
the attacker's refresh token survived it for seven days. token_version rides in
both JWTs and refresh refuses a stale one.

Bumped by all three password paths and by deactivation. Three ways to change a
password where one ends sessions and two do not is a rule nobody remembers.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: The single-use token table

A hashed, expiring, single-use secret bound to a user. Named `auth_token` with
a `kind` column rather than `password_reset_token`, because invitations (P3)
are the same shape and a second table would be a copy.

**Files:**
- Modify: `backend/app/models/enums.py`
- Create: `backend/app/models/auth_token.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/0026_auth_token.py`
- Create: `backend/app/services/auth_tokens.py`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/test_auth_tokens.py`

**Interfaces:**
- Consumes: `User` from `app.models.user`.
- Produces:
  - `AuthTokenKind(StrEnum)`: `password_reset`
  - `AuthToken` model: `id`, `kind`, `token_hash`, `user_id`, `expires_at`, `used_at`, `created_at`
  - `hash_token(raw: str) -> str` — SHA-256 hex
  - `issue(db, *, user: User, kind: AuthTokenKind, ttl: timedelta) -> str` — returns the **raw** token; supersedes outstanding ones of the same kind; commits.
  - `redeem(db, *, raw: str, kind: AuthTokenKind) -> AuthToken` — marks used and returns the row; raises `TokenInvalid` for absent, expired, used, or wrong kind. Commits.
  - `TokenInvalid(Exception)`
  - `RESET_TTL = timedelta(hours=1)`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_auth_tokens.py`:

```python
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


async def test_redeem_returns_the_row_for_the_right_user(
    session_factory, admin_user: User
) -> None:
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


async def test_issuing_again_kills_the_earlier_token(
    session_factory, admin_user: User
) -> None:
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


async def test_hash_token_is_stable_and_hex(admin_user: User) -> None:
    assert hash_token("abc") == hash_token("abc")
    assert len(hash_token("abc")) == 64
    int(hash_token("abc"), 16)


async def test_expiry_is_stored_timezone_aware(session_factory, admin_user: User) -> None:
    async with session_factory() as db:
        await issue(db, user=admin_user, kind=KIND, ttl=RESET_TTL)
        row = (await db.execute(select(AuthToken))).scalar_one()
    # SQLite returns naive; the service must compare in UTC either way. This
    # pins that expires_at is roughly one hour out, whatever the driver does.
    delta = row.expires_at.replace(tzinfo=UTC) - datetime.now(UTC)
    assert timedelta(minutes=55) < delta <= timedelta(hours=1)
```

- [ ] **Step 2: Add `AuthToken` to the SQLite table list**

In `backend/tests/conftest.py`, add the import beside the other models:

```python
from app.models.auth_token import AuthToken
```

and add `AuthToken.__table__` to the `tables=[…]` list in `session_factory`.
(The `InstanceSettings` table is added in Task 6, where it is first needed.)

- [ ] **Step 3: Run the tests to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_auth_tokens.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.auth_token'`.

- [ ] **Step 4: Add the enum**

In `backend/app/models/enums.py`, beside `SmtpSecurity`:

```python
class AuthTokenKind(enum.StrEnum):
    """What a single-use ``auth_token`` row is for."""

    password_reset = "password_reset"
```

Add `"AuthTokenKind"` to `__all__`.

- [ ] **Step 5: Add the model**

Create `backend/app/models/auth_token.py`:

```python
"""Single-use, expiring secrets bound to a user.

One table for every kind — password reset today, invitations next — because
they are the same shape: a hashed token, an owner, an expiry, and whether it
has been spent. A second table per kind would be a copy.

Only the hash is stored. A database leak must not hand over live reset links,
for the same reason it must not hand over passwords.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import AuthTokenKind
from app.models.mixins import IdMixin


class AuthToken(IdMixin, Base):
    """One issued token. ``used_at`` set means spent."""

    __tablename__ = "auth_token"

    kind: Mapped[AuthTokenKind] = mapped_column(
        Enum(
            AuthTokenKind,
            name="auth_token_kind",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    # SHA-256 hex. Unique so a lookup by hash is an index hit, and so two
    # tokens can never collide into one row.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # CASCADE: a deleted user's outstanding tokens are meaningless.
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["AuthToken"]
```

Register it in `backend/app/models/__init__.py` — add
`from app.models.auth_token import AuthToken  # noqa: F401` in alphabetical
position and `"AuthToken"` to `__all__`.

- [ ] **Step 6: Write the migration**

Create `backend/alembic/versions/0026_auth_token.py`:

```python
"""auth_token: single-use secrets bound to a user

Password reset today; invitations next. Only the hash is stored.

Revision ID: 0026_auth_token
Revises: 0025_token_version
Create Date: 2026-09-03 17:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_auth_token"
down_revision: str | None = "0025_token_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # create_table emits CREATE TYPE for the enum (add_column does not).
    op.create_table(
        "auth_token",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "kind",
            sa.Enum("password_reset", name="auth_token_kind"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_auth_token_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_token")),
    )
    op.create_index(op.f("ix_auth_token_token_hash"), "auth_token", ["token_hash"], unique=True)
    op.create_index(op.f("ix_auth_token_user_id"), "auth_token", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_auth_token_user_id"), table_name="auth_token")
    op.drop_index(op.f("ix_auth_token_token_hash"), table_name="auth_token")
    op.drop_table("auth_token")
    sa.Enum(name="auth_token_kind").drop(op.get_bind(), checkfirst=True)
```

- [ ] **Step 7: Write the service**

Create `backend/app/services/auth_tokens.py`:

```python
"""Issue and redeem single-use tokens.

The raw token leaves this module exactly once — as the return value of
:func:`issue`, on its way into an email — and is never stored or logged.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth_token import AuthToken
from app.models.enums import AuthTokenKind
from app.models.user import User

#: Long enough for a slow mail server and a distracted user; short enough that
#: a link found in a mailbox next week is dead.
RESET_TTL = timedelta(hours=1)


class TokenInvalid(Exception):
    """The token is absent, expired, spent, or of the wrong kind.

    One exception and one message for all four: distinguishing them tells an
    attacker which of their guesses were once valid.
    """

    def __init__(self) -> None:
        super().__init__("This link is invalid or has expired.")


def hash_token(raw: str) -> str:
    """SHA-256 hex of the raw token.

    Not Argon2: the token carries 256 bits of entropy, and a slow hash exists
    to protect low-entropy secrets. Here it would only slow the lookup.
    """
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


async def issue(
    db: AsyncSession, *, user: User, kind: AuthTokenKind, ttl: timedelta
) -> str:
    """Mint a token for ``user``, superseding any outstanding one of ``kind``.

    Returns the raw token. Commits.
    """
    now = _now()
    # Supersede first: every unexpired, unused token of this kind for this
    # user is spent. Two live links for one account is the state an attacker
    # requesting resets in parallel would try to create.
    await db.execute(
        update(AuthToken)
        .where(
            AuthToken.user_id == user.id,
            AuthToken.kind == kind,
            AuthToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    raw = secrets.token_urlsafe(32)
    db.add(
        AuthToken(
            kind=kind,
            token_hash=hash_token(raw),
            user_id=user.id,
            expires_at=now + ttl,
        )
    )
    await db.commit()
    return raw


async def redeem(db: AsyncSession, *, raw: str, kind: AuthTokenKind) -> AuthToken:
    """Spend a token and return its row, or raise :class:`TokenInvalid`. Commits."""
    row = (
        await db.execute(select(AuthToken).where(AuthToken.token_hash == hash_token(raw)))
    ).scalar_one_or_none()
    if row is None or row.kind != kind or row.used_at is not None:
        raise TokenInvalid()
    # SQLite hands back naive datetimes; Postgres hands back aware ones. Compare
    # in UTC either way rather than trusting the driver.
    expires_at = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
    if expires_at <= _now():
        raise TokenInvalid()
    row.used_at = _now()
    await db.commit()
    return row


__all__ = ["RESET_TTL", "TokenInvalid", "hash_token", "issue", "redeem"]
```

- [ ] **Step 8: Run the tests to verify they pass**

```bash
docker exec megoopm-test python -m pytest tests/test_auth_tokens.py -p no:cacheprovider -p no:warnings
```
Expected: PASS, 10 tests.

- [ ] **Step 9: Commit**

```bash
git add backend/app/models/enums.py backend/app/models/auth_token.py backend/app/models/__init__.py \
        backend/alembic/versions/0026_auth_token.py backend/app/services/auth_tokens.py \
        backend/tests/conftest.py backend/tests/test_auth_tokens.py
git commit -m "feat(auth): single-use token table

Hashed, expiring, spent-once, and superseded by the next issue. One table with
a kind column: invitations are the same shape, and a second table would be a
copy. Only the SHA-256 is stored — a database leak must not hand over live
reset links, for the same reason it must not hand over passwords.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The client-IP rule

uvicorn runs without `--proxy-headers`, and port 8000 is both proxied by nginx
and published directly. So `request.client.host` is sometimes the proxy and
sometimes the real client. One helper decides which, with tests for both.

**Files:**
- Create: `backend/app/core/client_ip.py`
- Test: `backend/tests/test_client_ip.py`

**Interfaces:**
- Consumes: `starlette.requests.Request`.
- Produces: `client_ip(request: Request) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_client_ip.py`:

```python
from __future__ import annotations

from app.core.client_ip import client_ip
from starlette.requests import Request


def _request(*, client_host: str, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (client_host, 12345),
    }
    return Request(scope)


def test_a_direct_public_client_is_its_own_address() -> None:
    assert client_ip(_request(client_host="203.0.113.7")) == "203.0.113.7"


def test_a_public_client_cannot_spoof_the_header() -> None:
    # The header is ignored unless the connection came from a trusted range.
    req = _request(client_host="203.0.113.7", forwarded="10.0.0.1")
    assert client_ip(req) == "203.0.113.7"


def test_behind_the_proxy_the_forwarded_address_wins() -> None:
    # nginx in the compose network connects from a private address and appends
    # the real client to X-Forwarded-For.
    req = _request(client_host="172.18.0.5", forwarded="203.0.113.7")
    assert client_ip(req) == "203.0.113.7"


def test_the_rightmost_forwarded_address_is_used() -> None:
    # A client that sent its own X-Forwarded-For has it *prepended* to; the
    # address nginx appended — the one that actually connected to it — is last.
    req = _request(client_host="172.18.0.5", forwarded="1.2.3.4, 203.0.113.7")
    assert client_ip(req) == "203.0.113.7"


def test_loopback_is_trusted_too() -> None:
    req = _request(client_host="127.0.0.1", forwarded="203.0.113.7")
    assert client_ip(req) == "203.0.113.7"


def test_a_private_client_with_no_header_is_itself() -> None:
    # An operator on the LAN hitting port 8000 directly.
    assert client_ip(_request(client_host="192.168.1.20")) == "192.168.1.20"


def test_a_garbage_header_falls_back_to_the_connection() -> None:
    req = _request(client_host="172.18.0.5", forwarded="not an address")
    assert client_ip(req) == "172.18.0.5"


def test_no_client_at_all_is_unknown() -> None:
    # ASGI test transports sometimes omit `client`. Never raise here — this
    # runs on an unauthenticated route.
    req = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    assert client_ip(req) == "unknown"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_client_ip.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.client_ip'`.

- [ ] **Step 3: Write the helper**

Create `backend/app/core/client_ip.py`:

```python
"""Which address a request really came from.

uvicorn runs without ``--proxy-headers``, and port 8000 is both proxied by
nginx and published directly. So ``request.client.host`` is the proxy for one
path and the real client for the other.

The rule: trust ``X-Forwarded-For`` only when the connection itself came from a
private range (RFC 1918, loopback) — that is nginx in the compose network, or
an operator on the LAN. A public client's header is ignored, so it cannot spoof
its way into another bucket. A LAN attacker forging the header is outside a
rate limit's threat model.

Reads the *rightmost* forwarded address: nginx appends the address that
connected to it, so anything a client prepended sits to the left.
"""

from __future__ import annotations

import ipaddress

from starlette.requests import Request

_TRUSTED = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
)


def _is_trusted(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in _TRUSTED)


def _valid(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def client_ip(request: Request) -> str:
    """The address to rate-limit on. Never raises."""
    connecting = request.client.host if request.client else None
    if not connecting:
        return "unknown"
    if not _is_trusted(connecting):
        return connecting
    forwarded = request.headers.get("x-forwarded-for", "")
    candidates = [part.strip() for part in forwarded.split(",") if part.strip()]
    if candidates and _valid(candidates[-1]):
        return candidates[-1]
    return connecting


__all__ = ["client_ip"]
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
docker exec megoopm-test python -m pytest tests/test_client_ip.py -p no:cacheprovider -p no:warnings
```
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/client_ip.py backend/tests/test_client_ip.py
git commit -m "feat(core): the client-IP rule for a service that is both proxied and direct

Trust X-Forwarded-For only when the connection came from a private range. A
public client's header is ignored, so it cannot spoof its bucket; nginx in the
compose network is private, so its header is read. The first place the app
reads the header, written to be reused.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The rate limiter

Two counters in Redis. Fails closed. The client is injectable so the tests run
against an in-memory fake and need no Redis.

**Files:**
- Create: `backend/app/services/rate_limit.py`
- Test: `backend/tests/test_rate_limit.py`

**Interfaces:**
- Consumes: `redis.asyncio`, `settings.redis_url`.
- Produces:
  - `RateLimited(Exception)` with `.retry_after: int` seconds
  - `RateLimitUnavailable(Exception)`
  - `hit(client, key: str, *, limit: int, window_s: int) -> None`
  - `check_password_reset(*, email: str, ip: str, client=None) -> None` — 3 per address per hour, 10 per IP per hour; `client=None` opens a real one.
  - `check_password_reset_redeem(*, ip: str, client=None) -> None` — 10 per IP per hour, for the route that has a token but no address.
  - `RESET_EMAIL_LIMIT = 3`, `RESET_IP_LIMIT = 10`, `RESET_WINDOW_S = 3600`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_rate_limit.py`:

```python
"""The limiter against an in-memory fake. No Redis needed."""

from __future__ import annotations

import pytest
from app.services.rate_limit import (
    RESET_EMAIL_LIMIT,
    RESET_IP_LIMIT,
    RateLimited,
    RateLimitUnavailable,
    check_password_reset,
    check_password_reset_redeem,
    hit,
)
from redis.exceptions import ConnectionError as RedisConnectionError


class FakeRedis:
    """The three commands the limiter uses, over a dict."""

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
        return self.ttls.get(key, -1)

    async def aclose(self) -> None:
        pass


class DeadRedis(FakeRedis):
    async def incr(self, key: str) -> int:
        raise RedisConnectionError("Connection refused")


async def test_the_first_hits_pass() -> None:
    client = FakeRedis()
    for _ in range(3):
        await hit(client, "k", limit=3, window_s=60)


async def test_the_hit_over_the_limit_is_refused_with_a_retry_after() -> None:
    client = FakeRedis()
    for _ in range(3):
        await hit(client, "k", limit=3, window_s=60)
    with pytest.raises(RateLimited) as info:
        await hit(client, "k", limit=3, window_s=60)
    assert info.value.retry_after == 60


async def test_the_window_is_set_on_the_first_hit_only() -> None:
    # Re-setting EXPIRE on every hit would keep a busy key alive forever.
    client = FakeRedis()
    await hit(client, "k", limit=3, window_s=60)
    client.ttls["k"] = 10  # pretend fifty seconds passed
    await hit(client, "k", limit=3, window_s=60)
    assert client.ttls["k"] == 10


async def test_redis_down_fails_closed() -> None:
    # A security control on a security appliance; "allow everything" is the
    # wrong default when the control cannot be consulted.
    with pytest.raises(RateLimitUnavailable):
        await hit(DeadRedis(), "k", limit=3, window_s=60)


async def test_password_reset_limits_the_address() -> None:
    client = FakeRedis()
    for _ in range(RESET_EMAIL_LIMIT):
        await check_password_reset(email="a@example.com", ip="1.1.1.1", client=client)
    with pytest.raises(RateLimited):
        await check_password_reset(email="a@example.com", ip="1.1.1.1", client=client)


async def test_password_reset_limits_the_ip_across_addresses() -> None:
    # One client cycling through addresses is what the per-IP limit stops.
    client = FakeRedis()
    for i in range(RESET_IP_LIMIT):
        await check_password_reset(email=f"u{i}@example.com", ip="1.1.1.1", client=client)
    with pytest.raises(RateLimited):
        await check_password_reset(email="new@example.com", ip="1.1.1.1", client=client)


async def test_the_address_key_is_case_insensitive() -> None:
    # Login is case-insensitive on email; the limit must be too, or
    # A@x.com and a@x.com are six requests instead of three.
    client = FakeRedis()
    for _ in range(RESET_EMAIL_LIMIT):
        await check_password_reset(email="A@Example.com", ip="1.1.1.1", client=client)
    with pytest.raises(RateLimited):
        await check_password_reset(email="a@example.com", ip="1.1.1.1", client=client)


async def test_redeem_is_limited_per_ip() -> None:
    # The reset-password route has a token, not an address. Its limit exists
    # so a token cannot be brute-forced from one client.
    client = FakeRedis()
    for _ in range(RESET_IP_LIMIT):
        await check_password_reset_redeem(ip="1.1.1.1", client=client)
    with pytest.raises(RateLimited):
        await check_password_reset_redeem(ip="1.1.1.1", client=client)


async def test_the_address_is_not_stored_in_the_key() -> None:
    # Redis keys are visible to anyone with Redis access; the address is hashed.
    client = FakeRedis()
    await check_password_reset(email="secret@example.com", ip="1.1.1.1", client=client)
    assert not any("secret@example.com" in key for key in client.values)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_rate_limit.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.rate_limit'`.

- [ ] **Step 3: Write the limiter**

Create `backend/app/services/rate_limit.py`:

```python
"""Fixed-window counters in Redis, for the unauthenticated email-sending routes.

An endpoint that sends email with no limit is an outbound spam cannon: request
resets for a thousand addresses and the mail server delivers to all of them,
which is how a sending domain ends up on a blocklist.

Fails closed. This is a security control on a security appliance, and a Redis
outage already takes Celery with it — degrading silently is the wrong default.
"""

from __future__ import annotations

import hashlib

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.core.config import settings

RESET_EMAIL_LIMIT = 3
RESET_IP_LIMIT = 10
RESET_WINDOW_S = 3600

_PREFIX = "megoopm:ratelimit"


class RateLimited(Exception):
    """Over the limit. ``retry_after`` is seconds until the window resets."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("Too many requests")
        self.retry_after = max(1, retry_after)


class RateLimitUnavailable(Exception):
    """Redis could not be consulted. The caller fails closed."""


def _client() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def hit(client: aioredis.Redis, key: str, *, limit: int, window_s: int) -> None:
    """Count one hit on ``key``; raise when it exceeds ``limit`` in ``window_s``."""
    try:
        count = await client.incr(key)
        if count == 1:
            # Only on the first hit. Re-arming EXPIRE on every hit would keep
            # a busy key alive forever, turning a window into a permanent ban.
            await client.expire(key, window_s)
        if count > limit:
            ttl = await client.ttl(key)
            raise RateLimited(retry_after=ttl if ttl > 0 else window_s)
    except (RedisError, OSError) as exc:
        raise RateLimitUnavailable(str(exc)) from exc


async def check_password_reset(
    *, email: str, ip: str, client: aioredis.Redis | None = None
) -> None:
    """Both password-reset limits. Raises :class:`RateLimited` or
    :class:`RateLimitUnavailable`; returns silently when allowed."""
    # Hashed: Redis keys are visible to anyone with Redis access, and an
    # address is personal data. Lower-cased first, as login is.
    email_key = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()
    own = client is None
    redis_client = client if client is not None else _client()
    try:
        await hit(redis_client, f"{_PREFIX}:reset:email:{email_key}",
                  limit=RESET_EMAIL_LIMIT, window_s=RESET_WINDOW_S)
        await hit(redis_client, f"{_PREFIX}:reset:ip:{ip}",
                  limit=RESET_IP_LIMIT, window_s=RESET_WINDOW_S)
    finally:
        if own:
            await redis_client.aclose()


async def check_password_reset_redeem(*, ip: str, client: aioredis.Redis | None = None) -> None:
    """The per-IP limit for spending a token. No address to key on here; the
    point is that a token cannot be brute-forced from one client."""
    own = client is None
    redis_client = client if client is not None else _client()
    try:
        await hit(redis_client, f"{_PREFIX}:reset-redeem:ip:{ip}",
                  limit=RESET_IP_LIMIT, window_s=RESET_WINDOW_S)
    finally:
        if own:
            await redis_client.aclose()


__all__ = [
    "RESET_EMAIL_LIMIT",
    "RESET_IP_LIMIT",
    "RESET_WINDOW_S",
    "RateLimitUnavailable",
    "RateLimited",
    "check_password_reset",
    "check_password_reset_redeem",
    "hit",
]
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
docker exec megoopm-test python -m pytest tests/test_rate_limit.py -p no:cacheprovider -p no:warnings
```
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/rate_limit.py backend/tests/test_rate_limit.py
git commit -m "feat(auth): rate limiter for the email-sending routes

Fixed windows in Redis, three per address and ten per IP per hour. Fails
closed: a security control on a security appliance must not degrade to
'allow everything' when it cannot be consulted.

The address is hashed in the key — Redis keys are visible to anyone with Redis
access, and an email address is personal data.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: The mail task and the two templates

The first real Celery task, which P1 deferred for lack of a caller. Two
templates: the link, and the notice that tells a victim someone else completed
a reset.

**Files:**
- Create: `backend/app/tasks/mail.py`
- Modify: `backend/app/core/celery_app.py`
- Create: `backend/app/services/mail/templates/password_reset.html.j2`, `.txt.j2`
- Create: `backend/app/services/mail/templates/password_changed.html.j2`, `.txt.j2`
- Test: `backend/tests/test_mail_task.py`
- Test: `backend/tests/test_mail_templates.py` (append)

**Interfaces:**
- Consumes: `render`, `APP_NAME` from `app.services.mail.templates`; `send_email` from `app.services.mail.sender`; `mail_config_from_row`, `get_instance_settings` from `app.services.instance_settings`.
- Produces:
  - Celery task `app.tasks.mail.send_email` with signature `(to: str, template: str, subject: str, context: dict) -> dict`.
  - Templates `password_reset` (context: `app_name`, `reset_url`, `ttl_minutes`) and `password_changed` (context: `app_name`).

- [ ] **Step 1: Write the failing template tests**

Append to `backend/tests/test_mail_templates.py`:

```python
# --- password reset -------------------------------------------------------


def test_reset_email_carries_the_link_in_both_bodies() -> None:
    email = render(
        "password_reset",
        subject="Reset",
        app_name="MegooPM",
        reset_url="https://pm.example.com/reset-password?token=abc",
        ttl_minutes=60,
    )
    assert "https://pm.example.com/reset-password?token=abc" in email.html
    assert "https://pm.example.com/reset-password?token=abc" in email.text


def test_reset_email_states_the_expiry() -> None:
    email = render(
        "password_reset",
        subject="Reset",
        app_name="MegooPM",
        reset_url="https://x/r?token=abc",
        ttl_minutes=60,
    )
    assert "60 minutes" in email.text


def test_reset_url_is_not_html_escaped_into_a_broken_link() -> None:
    # `&` in a query string must survive; `&amp;` inside an href is fine for a
    # browser, but the *text* body has no parser and must be raw.
    url = "https://x/r?token=abc&x=1"
    email = render("password_reset", subject="Reset", app_name="MegooPM",
                   reset_url=url, ttl_minutes=60)
    assert url in email.text


def test_changed_notice_has_no_link() -> None:
    # It exists to tell a victim someone else completed a reset. A link in it
    # would make it phishable.
    email = render("password_changed", subject="Changed", app_name="MegooPM")
    assert "href=" not in email.html
    assert "http" not in email.text
```

- [ ] **Step 2: Write the failing task tests**

Create `backend/tests/test_mail_task.py`:

```python
"""The Celery send task: registered, and renders-then-sends."""

from __future__ import annotations

import pytest
from app.core.celery_app import celery_app
from app.services.mail import sender as sender_module
from app.models.enums import SmtpSecurity
from app.services.mail.config import MailConfig
from app.tasks import mail as mail_tasks


def test_the_task_is_registered_with_the_worker() -> None:
    # The existing guard checks only beat_schedule. This task is dispatched
    # with .delay() and would slip past it — and a missing TASK_MODULES entry
    # shows up as the first reset email silently never sending.
    celery_app.loader.import_default_modules()
    assert "app.tasks.mail.send_email" in celery_app.tasks


def test_it_renders_and_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict = {}

    def fake_send(config, *, to, email, timeout=15.0):
        sent["to"] = to
        sent["subject"] = email.subject
        sent["html"] = email.html

    monkeypatch.setattr(sender_module, "send_email", fake_send)
    monkeypatch.setattr(
        mail_tasks,
        "_load_config",
        lambda: MailConfig(
            host="mail.example.com", port=587, security=SmtpSecurity.starttls,
            username=None, password=None,
            from_address="megoopm@example.com", from_name="MegooPM",
        ),
    )

    result = mail_tasks.send_email(
        to="ops@example.com",
        template="password_changed",
        subject="Your password was changed",
        context={"app_name": "MegooPM"},
    )

    assert result == {"sent": True, "to": "ops@example.com"}
    assert sent["to"] == "ops@example.com"
    assert sent["subject"] == "Your password was changed"
    assert "password" in sent["html"].lower()
```

- [ ] **Step 3: Run them to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_mail_task.py tests/test_mail_templates.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — `No module named 'app.tasks.mail'` and `TemplateNotFound`.

- [ ] **Step 4: Write the templates**

Create `backend/app/services/mail/templates/password_reset.html.j2`:

```jinja
{% extends "base.html.j2" %}
{% block subject_text %}Reset your {{ app_name }} password{% endblock %}
{% block body %}
<p class="m-accent" style="margin:0 0 16px 0;font-size:18px;font-weight:600;
                           color:{{ light.primary }};">Reset your password</p>
<p style="margin:0 0 20px 0;">
  Someone asked to reset the password for this {{ app_name }} account. If that
  was you, use the button below. The link works once and expires in
  {{ ttl_minutes }} minutes.
</p>
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 20px 0;">
  <tr>
    <td class="m-btn" style="background:{{ light.primary }};border-radius:8px;">
      <a href="{{ reset_url }}"
         style="display:inline-block;padding:11px 20px;color:{{ light.primary_foreground }};
                text-decoration:none;font-weight:600;font-size:14px;">Choose a new password</a>
    </td>
  </tr>
</table>
<p class="m-muted" style="margin:0 0 8px 0;color:{{ light.muted_foreground }};font-size:13px;">
  If the button does not work, paste this into your browser:
</p>
<p class="m-muted" style="margin:0 0 20px 0;color:{{ light.muted_foreground }};font-size:12px;
                          word-break:break-all;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;">
  {{ reset_url }}
</p>
<p class="m-muted" style="margin:0;color:{{ light.muted_foreground }};font-size:13px;">
  If you did not ask for this, you can ignore this email. Your password has not
  changed.
</p>
{% endblock %}
```

Create `backend/app/services/mail/templates/password_reset.txt.j2`:

```jinja
Reset your password

Someone asked to reset the password for this {{ app_name }} account. If that
was you, open this link. It works once and expires in {{ ttl_minutes }} minutes.

{{ reset_url }}

If you did not ask for this, you can ignore this email. Your password has not
changed.

--
Sent by {{ app_name }}.
```

Create `backend/app/services/mail/templates/password_changed.html.j2`:

```jinja
{% extends "base.html.j2" %}
{% block subject_text %}Your {{ app_name }} password was changed{% endblock %}
{% block body %}
<p class="m-accent" style="margin:0 0 16px 0;font-size:18px;font-weight:600;
                           color:{{ light.primary }};">Your password was changed</p>
<p style="margin:0 0 16px 0;">
  The password for this {{ app_name }} account was just changed, and every
  other session was signed out.
</p>
<p class="m-muted" style="margin:0;color:{{ light.muted_foreground }};font-size:13px;">
  If this was you, there is nothing to do. If it was not, contact an
  administrator of {{ app_name }} straight away.
</p>
{% endblock %}
```

Create `backend/app/services/mail/templates/password_changed.txt.j2`:

```jinja
Your password was changed

The password for this {{ app_name }} account was just changed, and every other
session was signed out.

If this was you, there is nothing to do. If it was not, contact an
administrator of {{ app_name }} straight away.

--
Sent by {{ app_name }}.
```

- [ ] **Step 5: Write the task**

Create `backend/app/tasks/mail.py`:

```python
"""Send one email from a worker.

The HTTP request that queued this has already returned. A slow or dead mail
server therefore never fails a user-facing action — the user's password is
reset either way, and the email arrives when it arrives.

Retries three times with backoff on any failure, then gives up and logs. A
reset link that never arrives after a mail-server outage is the user clicking
"forgot password" again, which the rate limit permits.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings
from app.services import instance_settings as settings_service
from app.services.mail import sender as mail_sender
from app.services.mail.config import MailConfig
from app.services.mail.templates import render

log = logging.getLogger(__name__)


async def _load_config_async() -> MailConfig:
    engine = create_async_engine(settings.database_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            row = await settings_service.get_instance_settings(session)
            return settings_service.mail_config_from_row(row)
    finally:
        await engine.dispose()


def _load_config() -> MailConfig:
    """Read the SMTP config. Separate so a test can replace it."""
    return asyncio.run(_load_config_async())


@celery_app.task(
    name="app.tasks.mail.send_email",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def send_email(self, *, to: str, template: str, subject: str, context: dict) -> dict:
    """Render ``template`` with ``context`` and send it to ``to``."""
    config = _load_config()
    email = render(template, subject=subject, **context)
    mail_sender.send_email(config, to=to, email=email)
    log.info("sent %s to %s", template, to)
    return {"sent": True, "to": to}


__all__ = ["send_email"]
```

- [ ] **Step 6: Register it**

In `backend/app/core/celery_app.py`, add `"app.tasks.mail",` to `TASK_MODULES`.

- [ ] **Step 7: Run the tests to verify they pass**

```bash
docker exec megoopm-test python -m pytest tests/test_mail_task.py tests/test_mail_templates.py -p no:cacheprovider -p no:warnings
```
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/tasks/mail.py backend/app/core/celery_app.py \
        backend/app/services/mail/templates/ backend/tests/test_mail_task.py \
        backend/tests/test_mail_templates.py
git commit -m "feat(mail): the send task, and the two password-reset emails

The first real Celery task; P1 deferred it for lack of a caller. Registered in
TASK_MODULES with a test that proves it — the existing guard checks only
beat_schedule, and this one is dispatched with .delay().

The changed-password notice carries no link on purpose: it exists to tell a
victim someone else completed a reset, and a link would make it phishable.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: The three routes

`forgot-password`, `reset-password`, `capabilities`. The first two are
rate-limited; the first never reveals whether an address exists.

**Files:**
- Modify: `backend/app/schemas/auth.py`
- Modify: `backend/app/api/routes/auth.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/openapi.json` (regenerated)
- Test: `backend/tests/test_password_reset_api.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces:
  - `POST /auth/forgot-password` → 202 `{"detail": NEUTRAL}`; 429; 503
  - `POST /auth/reset-password` → 204; 400 on a refused token; 429; 503
  - `GET /auth/capabilities` → `{"password_reset": bool}`
  - `NEUTRAL_MESSAGE = "If that address is registered, a reset link is on its way."`

- [ ] **Step 1: Add `InstanceSettings` to the SQLite table list**

`capabilities` and `forgot-password` read the settings row. In
`backend/tests/conftest.py`, import `InstanceSettings` from
`app.models.instance_settings` and add `InstanceSettings.__table__` to the
`tables=[…]` list. Its columns are all portable types; the foreign keys to
`custom_pages` are declared but SQLite does not enforce them by default, so the
missing target table is not an error.

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_password_reset_api.py`:

```python
"""The three password-reset routes. SQLite-backed; Redis and SMTP are faked."""

from __future__ import annotations

import pytest
from app.api.routes import auth as auth_routes
from app.models.enums import SmtpSecurity
from app.models.instance_settings import InstanceSettings
from app.models.user import User
from app.services import rate_limit
from app.services.auth_tokens import RESET_TTL, hash_token, issue
from app.models.enums import AuthTokenKind
from app.models.auth_token import AuthToken
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
    assert resp.json() == {"password_reset": True}


async def test_capabilities_false_without_smtp(db_client: AsyncClient, mail_unconfigured) -> None:
    resp = await db_client.get(CAPABILITIES)
    assert resp.json() == {"password_reset": False}


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
    assert "https://pm.example.com/reset-password?token=" in mail.calls[0]["context"]["reset_url"]

    async with session_factory() as db:
        rows = (await db.execute(select(AuthToken))).scalars().all()
    assert len(rows) == 1
    # The raw token in the link must hash to the stored row.
    raw = mail.calls[0]["context"]["reset_url"].split("token=")[1]
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
    class Dead:
        async def incr(self, key):
            from redis.exceptions import ConnectionError as E

            raise E("refused")

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
    before = await db_client.post(LOGIN, json={"email": admin_user.email, "password": "adminpass123"})
    raw = await _issue_for(session_factory, admin_user)

    resp = await db_client.post(RESET, json={"token": raw, "new_password": "brandnew12345"})

    assert resp.status_code == 204, resp.text
    # New password works.
    after = await db_client.post(LOGIN, json={"email": admin_user.email, "password": "brandnew12345"})
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
    fresh = await db_client.post(LOGIN, json={"email": admin_user.email, "password": "brandnew12345"})
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
```

- [ ] **Step 3: Run them to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_password_reset_api.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — 404 on the three routes, and `AttributeError` for
`auth_routes.send_email_task`.

- [ ] **Step 4: Add the schemas**

Append to `backend/app/schemas/auth.py`:

```python
class ForgotPasswordRequest(BaseModel):
    """Body for ``POST /auth/forgot-password``."""

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Body for ``POST /auth/reset-password``."""

    token: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=128)


class AuthCapabilities(BaseModel):
    """What the login page may offer before anyone is signed in."""

    password_reset: bool


class NeutralResponse(BaseModel):
    """The one body ``forgot-password`` ever returns."""

    detail: str
```

Extend `__all__` with the four names.

- [ ] **Step 5: Add the routes**

In `backend/app/api/routes/auth.py`, extend the imports:

```python
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import CurrentUser, SessionDep
from app.core.client_ip import client_ip
from app.models.enums import AuditAction, AuthTokenKind
from app.schemas.auth import (
    AuthCapabilities,
    ForgotPasswordRequest,
    LoginRequest,
    NeutralResponse,
    RefreshRequest,
    ResetPasswordRequest,
    TokenPair,
)
from app.services import auth_tokens, instance_settings as settings_service, rate_limit
from app.services.audit import record_audit
from app.services.mail.templates import APP_NAME
from app.tasks.mail import send_email as send_email_task
```

(Keep the existing `jwt`, `security`, `UserRead`, `user_service`, and `User`
imports.) Then add the constant and the three routes after `read_me`:

```python
NEUTRAL_MESSAGE = "If that address is registered, a reset link is on its way."


async def _reset_available(db) -> tuple[bool, str | None]:
    """Whether a reset link can be built and sent, and the app URL if so."""
    row = await settings_service.get_instance_settings(db)
    if row.smtp_enabled and row.app_url:
        return True, row.app_url
    return False, None


@router.get("/capabilities", response_model=AuthCapabilities)
async def capabilities(db: SessionDep) -> AuthCapabilities:
    """What the login page may offer. Unauthenticated by necessity.

    Leaks one bit — whether email is configured — which is cheaper than a user
    clicking "forgot password", being told to check their inbox, and nothing
    ever arriving.
    """
    available, _ = await _reset_available(db)
    return AuthCapabilities(password_reset=available)


def _limit(exc: rate_limit.RateLimited) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many requests. Try again later.",
        headers={"Retry-After": str(exc.retry_after)},
    )


def _unavailable() -> HTTPException:
    # A fresh instance each time: raising one shared exception object from
    # several places leaves the last traceback attached to all of them.
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Password reset is temporarily unavailable.",
    )


@router.post(
    "/forgot-password", response_model=NeutralResponse, status_code=status.HTTP_202_ACCEPTED
)
async def forgot_password(
    body: ForgotPasswordRequest, request: Request, db: SessionDep
) -> NeutralResponse:
    """Issue a reset link if the address belongs to an active account.

    Returns the same status and body whether or not it does. Otherwise this
    page is a directory of who has an account, for anyone who can reach it.
    Response *timing* still differs slightly; the spec records that as a
    known, accepted gap.
    """
    try:
        await rate_limit.check_password_reset(email=body.email, ip=client_ip(request))
    except rate_limit.RateLimited as exc:
        raise _limit(exc) from None
    except rate_limit.RateLimitUnavailable:
        raise _unavailable() from None

    available, app_url = await _reset_available(db)
    user = await user_service.get_by_email(db, body.email)
    if available and user is not None and user.is_active:
        raw = await auth_tokens.issue(
            db, user=user, kind=AuthTokenKind.password_reset, ttl=auth_tokens.RESET_TTL
        )
        send_email_task.delay(
            to=user.email,
            template="password_reset",
            subject=f"Reset your {APP_NAME} password",
            context={
                "app_name": APP_NAME,
                "reset_url": f"{app_url}/reset-password?token={raw}",
                "ttl_minutes": int(auth_tokens.RESET_TTL.total_seconds() // 60),
            },
        )
    return NeutralResponse(detail=NEUTRAL_MESSAGE)


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(body: ResetPasswordRequest, request: Request, db: SessionDep) -> None:
    """Spend a reset token and set the new password. Ends every open session."""
    try:
        await rate_limit.check_password_reset_redeem(ip=client_ip(request))
    except rate_limit.RateLimited as exc:
        raise _limit(exc) from None
    except rate_limit.RateLimitUnavailable:
        raise _unavailable() from None

    try:
        row = await auth_tokens.redeem(db, raw=body.token, kind=AuthTokenKind.password_reset)
    except auth_tokens.TokenInvalid as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    user = await user_service.get_by_id(db, row.user_id)
    if user is None or not user.is_active:
        # The token was valid a moment ago and the account is gone or off.
        # Same message as every other refusal.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(auth_tokens.TokenInvalid())
        )

    # set_password bumps token_version, which is what ends the other sessions.
    await user_service.set_password(db, user, body.new_password)
    await record_audit(
        db,
        actor=user.email,
        action=AuditAction.update,
        object_type="user",
        object_id=user.id,
        meta={"password_reset_via_email": True},
    )
    await db.commit()
    send_email_task.delay(
        to=user.email,
        template="password_changed",
        subject=f"Your {APP_NAME} password was changed",
        context={"app_name": APP_NAME},
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
docker exec megoopm-test python -m pytest tests/test_password_reset_api.py tests/test_auth.py -p no:cacheprovider -p no:warnings
```
Expected: PASS.

- [ ] **Step 7: Regenerate OpenAPI, lint, run everything**

```bash
docker exec megoopm-test python -m scripts.export_openapi
docker exec megoopm-test ruff check app tests
docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings
```
Expected: the whole suite passes, including `test_openapi.py`.

- [ ] **Step 8: Commit**

```bash
git add backend/app/schemas/auth.py backend/app/api/routes/auth.py \
        backend/tests/conftest.py backend/tests/test_password_reset_api.py backend/openapi.json
git commit -m "feat(auth): forgot-password, reset-password, and capabilities

forgot-password returns the identical status and body for a registered
address, an unknown one, and an inactive account — otherwise the login page is
a directory of who has an account. Both routes are rate-limited and fail
closed when Redis is unreachable.

capabilities leaks one bit to anonymous visitors, whether email is configured,
which is cheaper than a user being told to check an inbox nothing will reach.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: The frontend

A link on the login form, gated on capabilities; two pages; both registered as
public routes or the guard bounces them to `/login`.

**Files:**
- Modify: `frontend/src/lib/auth/api.ts`
- Modify: `frontend/src/lib/auth/session.ts`
- Modify: `frontend/src/lib/api/generated/schema.ts` (regenerated)
- Create: `frontend/src/components/auth/forgot-password-form.tsx`
- Create: `frontend/src/components/auth/reset-password-form.tsx`
- Create: `frontend/src/app/forgot-password/page.tsx`
- Create: `frontend/src/app/reset-password/page.tsx`
- Modify: `frontend/src/components/login-form.tsx`
- Test: `frontend/src/lib/auth/session.test.ts` (append)
- Test: `frontend/src/components/auth/forgot-password-form.test.tsx`
- Test: `frontend/src/components/auth/reset-password-form.test.tsx`
- Test: `frontend/src/components/login-form.test.tsx` (append)

**Interfaces:**
- Consumes: the three routes from Task 6.
- Produces:
  - `fetchCapabilities(): Promise<AuthCapabilities>`
  - `requestPasswordReset(email: string): Promise<void>`
  - `resetPassword(token: string, newPassword: string): Promise<void>`
  - `FORGOT_PASSWORD_ROUTE = "/forgot-password"`, `RESET_PASSWORD_ROUTE = "/reset-password"`

- [ ] **Step 1: Regenerate the types**

```bash
cd frontend && npm run gen:api
```

- [ ] **Step 2: Write the failing route test**

Append to `frontend/src/lib/auth/session.test.ts`:

```ts
describe("public routes for password reset", () => {
  it("lets anonymous visitors reach the forgot-password page", () => {
    // Otherwise the guard bounces them to /login before they can ask.
    expect(isPublicRoute("/forgot-password")).toBe(true);
  });

  it("lets anonymous visitors reach the reset-password page", () => {
    expect(isPublicRoute("/reset-password")).toBe(true);
  });
});
```

Add `isPublicRoute` to that file's import from `@/lib/auth/session` if missing.

- [ ] **Step 3: Run it to verify it fails**

```bash
cd frontend && npx vitest run src/lib/auth/session.test.ts
```
Expected: FAIL — both return `false`.

- [ ] **Step 4: Register the routes and add the API calls**

In `frontend/src/lib/auth/session.ts`:

```ts
/** Route where a user asks for a reset link. */
export const FORGOT_PASSWORD_ROUTE = "/forgot-password";

/** Route the emailed link lands on. */
export const RESET_PASSWORD_ROUTE = "/reset-password";

/** Routes that never require a session (login, health, static handled by matcher). */
export const PUBLIC_ROUTES: readonly string[] = [
  LOGIN_ROUTE,
  FORGOT_PASSWORD_ROUTE,
  RESET_PASSWORD_ROUTE,
];
```

Append to `frontend/src/lib/auth/api.ts`:

```ts
export type AuthCapabilities = Schemas["AuthCapabilities"];

/** What the login page may offer before anyone is signed in. */
export function fetchCapabilities(): Promise<AuthCapabilities> {
  return apiFetch<AuthCapabilities>("/api/v1/auth/capabilities", { method: "GET", token: null });
}

/**
 * Ask for a reset link. Resolves the same way whether or not the address is
 * registered — the backend never says, and neither must the page.
 */
export function requestPasswordReset(email: string): Promise<void> {
  return apiFetch<void>("/api/v1/auth/forgot-password", {
    method: "POST",
    body: { email },
    token: null,
  });
}

/** Spend a reset token. A refused token is a 400 with one message for every reason. */
export function resetPassword(token: string, newPassword: string): Promise<void> {
  return apiFetch<void>("/api/v1/auth/reset-password", {
    method: "POST",
    body: { token, new_password: newPassword },
    token: null,
  });
}
```

- [ ] **Step 5: Run the route test to verify it passes**

```bash
cd frontend && npx vitest run src/lib/auth/session.test.ts
```
Expected: PASS.

- [ ] **Step 6: Write the failing forgot-password tests**

Create `frontend/src/components/auth/forgot-password-form.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/lib/auth/api", () => ({ requestPasswordReset: vi.fn() }));

import { ApiError } from "@/lib/api/errors";
import { requestPasswordReset } from "@/lib/auth/api";
import { ForgotPasswordForm } from "@/components/auth/forgot-password-form";

afterEach(() => {
  cleanup();
  vi.mocked(requestPasswordReset).mockReset();
});

describe("ForgotPasswordForm", () => {
  it("shows the neutral message after a request", async () => {
    const user = userEvent.setup();
    vi.mocked(requestPasswordReset).mockResolvedValue(undefined);
    render(<ForgotPasswordForm />);

    await user.type(screen.getByLabelText("Email"), "me@example.com");
    await user.click(screen.getByRole("button", { name: /send reset link/i }));

    expect(await screen.findByText(/if that address is registered/i)).toBeInTheDocument();
  });

  it("shows the same message whatever the backend decided", async () => {
    // The page must not become a second oracle on top of the API.
    const user = userEvent.setup();
    vi.mocked(requestPasswordReset).mockResolvedValue(undefined);
    render(<ForgotPasswordForm />);

    await user.type(screen.getByLabelText("Email"), "nobody@example.com");
    await user.click(screen.getByRole("button", { name: /send reset link/i }));

    expect(await screen.findByText(/if that address is registered/i)).toBeInTheDocument();
    expect(screen.queryByText(/not found/i)).not.toBeInTheDocument();
  });

  it("says to wait on a rate limit", async () => {
    const user = userEvent.setup();
    vi.mocked(requestPasswordReset).mockRejectedValue(
      new ApiError(429, "Too many requests", { detail: "Too many requests. Try again later." }),
    );
    render(<ForgotPasswordForm />);

    await user.type(screen.getByLabelText("Email"), "me@example.com");
    await user.click(screen.getByRole("button", { name: /send reset link/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/too many/i);
  });

  it("links back to sign in", () => {
    render(<ForgotPasswordForm />);
    expect(screen.getByRole("link", { name: /back to sign in/i })).toHaveAttribute("href", "/login");
  });
});
```

- [ ] **Step 7: Write the failing reset-password tests**

Create `frontend/src/components/auth/reset-password-form.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

let token: string | null = "tok-123";
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(token ? { token } : {}),
}));

vi.mock("@/lib/auth/api", () => ({ resetPassword: vi.fn() }));

import { ApiError } from "@/lib/api/errors";
import { resetPassword } from "@/lib/auth/api";
import { ResetPasswordForm } from "@/components/auth/reset-password-form";

afterEach(() => {
  cleanup();
  vi.mocked(resetPassword).mockReset();
  token = "tok-123";
});

describe("ResetPasswordForm", () => {
  it("sends the token from the URL with the new password", async () => {
    const user = userEvent.setup();
    const reset = vi.mocked(resetPassword).mockResolvedValue(undefined);
    render(<ResetPasswordForm />);

    await user.type(screen.getByLabelText("New password"), "brandnew12345");
    await user.type(screen.getByLabelText("Confirm password"), "brandnew12345");
    await user.click(screen.getByRole("button", { name: /set new password/i }));

    expect(reset).toHaveBeenCalledWith("tok-123", "brandnew12345");
    expect(await screen.findByText(/password has been changed/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /sign in/i })).toHaveAttribute("href", "/login");
  });

  it("refuses mismatched passwords before sending anything", async () => {
    const user = userEvent.setup();
    const reset = vi.mocked(resetPassword);
    render(<ResetPasswordForm />);

    await user.type(screen.getByLabelText("New password"), "brandnew12345");
    await user.type(screen.getByLabelText("Confirm password"), "different12345");
    await user.click(screen.getByRole("button", { name: /set new password/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/match/i);
    expect(reset).not.toHaveBeenCalled();
  });

  it("explains a refused token and offers to start over", async () => {
    const user = userEvent.setup();
    vi.mocked(resetPassword).mockRejectedValue(
      new ApiError(400, "Bad request", { detail: "This link is invalid or has expired." }),
    );
    render(<ResetPasswordForm />);

    await user.type(screen.getByLabelText("New password"), "brandnew12345");
    await user.type(screen.getByLabelText("Confirm password"), "brandnew12345");
    await user.click(screen.getByRole("button", { name: /set new password/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/invalid or has expired/i);
    expect(screen.getByRole("link", { name: /request a new link/i })).toHaveAttribute(
      "href",
      "/forgot-password",
    );
  });

  it("says the link is incomplete when there is no token", () => {
    token = null;
    render(<ResetPasswordForm />);
    expect(screen.getByText(/link is incomplete/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /set new password/i })).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 8: Run both to verify they fail**

```bash
cd frontend && npx vitest run src/components/auth
```
Expected: FAIL — both modules fail to resolve.

- [ ] **Step 9: Write the forgot-password form and page**

Create `frontend/src/components/auth/forgot-password-form.tsx`:

```tsx
"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api/errors";
import { requestPasswordReset } from "@/lib/auth/api";
import { LOGIN_ROUTE } from "@/lib/auth/session";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const NEUTRAL = "If that address is registered, a reset link is on its way.";

/**
 * Ask for a reset link.
 *
 * Shows one message whatever happened. The backend never says whether the
 * address exists, and this page must not become a second oracle on top of it.
 */
export function ForgotPasswordForm() {
  const [email, setEmail] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await requestPasswordReset(email);
      setDone(true);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 429
          ? "Too many requests. Please wait a while and try again."
          : err instanceof ApiError
            ? err.detail
            : "Something went wrong. Please try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center p-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="space-y-1 text-center">
          <h1 className="text-xl font-semibold">Forgot your password?</h1>
          <p className="text-muted-foreground text-sm">
            Enter your email and we&apos;ll send you a link to choose a new one.
          </p>
        </div>

        {done ? (
          <p className="rounded-lg border p-4 text-sm">{NEUTRAL}</p>
        ) : (
          <form className="space-y-3" onSubmit={onSubmit} noValidate>
            <Input
              type="email"
              name="email"
              placeholder="Email"
              autoComplete="email"
              aria-label="Email"
              required
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={submitting}
            />
            {error ? (
              <p role="alert" className="text-destructive text-sm">
                {error}
              </p>
            ) : null}
            <Button type="submit" className="w-full" disabled={submitting || !email}>
              {submitting ? "Sending…" : "Send reset link"}
            </Button>
          </form>
        )}

        <p className="text-center text-sm">
          <Link href={LOGIN_ROUTE} className="text-primary underline-offset-4 hover:underline">
            Back to sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
```

Create `frontend/src/app/forgot-password/page.tsx`:

```tsx
import type { Metadata } from "next";

import { ForgotPasswordForm } from "@/components/auth/forgot-password-form";

export const metadata: Metadata = { title: "Forgot password" };

export default function ForgotPasswordPage() {
  return <ForgotPasswordForm />;
}
```

- [ ] **Step 10: Write the reset-password form and page**

Create `frontend/src/components/auth/reset-password-form.tsx`:

```tsx
"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api/errors";
import { resetPassword } from "@/lib/auth/api";
import { FORGOT_PASSWORD_ROUTE, LOGIN_ROUTE } from "@/lib/auth/session";
import { validateNewPassword } from "@/components/users/lib";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/**
 * The page the emailed link lands on.
 *
 * On success it sends the user to sign in rather than signing them in: the
 * token arrived by email, and spending it to mint a session would extend the
 * trust placed in that mailbox one step further than it needs to go.
 */
export function ResetPasswordForm() {
  const token = useSearchParams().get("token");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refused, setRefused] = useState(false);
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
      await resetPassword(token, password);
      setDone(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) {
        setRefused(true);
        setError(err.detail);
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
          <h1 className="text-xl font-semibold">Choose a new password</h1>
        </div>

        {!token ? (
          <p className="rounded-lg border p-4 text-sm">
            This link is incomplete. Open the link from your email again, or{" "}
            <Link href={FORGOT_PASSWORD_ROUTE} className="text-primary underline-offset-4 hover:underline">
              request a new link
            </Link>
            .
          </p>
        ) : done ? (
          <div className="space-y-3 rounded-lg border p-4 text-sm">
            <p>Your password has been changed and every other session was signed out.</p>
            <Link href={LOGIN_ROUTE} className="text-primary underline-offset-4 hover:underline">
              Sign in
            </Link>
          </div>
        ) : (
          <form className="space-y-3" onSubmit={onSubmit} noValidate>
            <Input
              type="password"
              name="new-password"
              placeholder="New password"
              autoComplete="new-password"
              aria-label="New password"
              required
              autoFocus
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
                {refused ? (
                  <>
                    {" "}
                    <Link
                      href={FORGOT_PASSWORD_ROUTE}
                      className="text-primary underline-offset-4 hover:underline"
                    >
                      Request a new link
                    </Link>
                    .
                  </>
                ) : null}
              </p>
            ) : null}
            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? "Saving…" : "Set new password"}
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
```

Create `frontend/src/app/reset-password/page.tsx`:

```tsx
import type { Metadata } from "next";
import { Suspense } from "react";

import { ResetPasswordForm } from "@/components/auth/reset-password-form";

export const metadata: Metadata = { title: "Reset password" };

/** Suspense because the form reads the token from the query string. */
export default function ResetPasswordPage() {
  return (
    <Suspense>
      <ResetPasswordForm />
    </Suspense>
  );
}
```

- [ ] **Step 11: Run both to verify they pass**

```bash
cd frontend && npx vitest run src/components/auth
```
Expected: PASS, 8 tests.

- [ ] **Step 12: Write the failing login-link tests**

Append to `frontend/src/components/login-form.test.tsx`:

```tsx
describe("LoginForm forgot-password link", () => {
  it("offers the link when the backend can send email", async () => {
    vi.mocked(fetchCapabilities).mockResolvedValue({ password_reset: true });
    render(<LoginForm />);

    expect(await screen.findByRole("link", { name: /forgot password/i })).toHaveAttribute(
      "href",
      "/forgot-password",
    );
  });

  it("hides the link when it could not work", async () => {
    // Clicking through to a page that says "check your inbox" when no email
    // will ever arrive is worse than no link.
    vi.mocked(fetchCapabilities).mockResolvedValue({ password_reset: false });
    render(<LoginForm />);
    await screen.findByLabelText("Email");

    expect(screen.queryByRole("link", { name: /forgot password/i })).not.toBeInTheDocument();
  });

  it("hides the link when capabilities cannot be fetched", async () => {
    vi.mocked(fetchCapabilities).mockRejectedValue(new Error("network"));
    render(<LoginForm />);
    await screen.findByLabelText("Email");

    expect(screen.queryByRole("link", { name: /forgot password/i })).not.toBeInTheDocument();
  });
});
```

The file already mocks `@/lib/auth/context` with a factory; add a second
mock beside it, before the component import:

```ts
vi.mock("@/lib/auth/api", () => ({ fetchCapabilities: vi.fn() }));
```

and `import { fetchCapabilities } from "@/lib/auth/api";` below it. Every
existing test in the file now renders with `fetchCapabilities` mocked but
unset, so add to the top-level `beforeEach`:

```ts
  vi.mocked(fetchCapabilities).mockResolvedValue({ password_reset: false });
```

The earlier tests are unaffected; the three new ones override it per test.

- [ ] **Step 13: Run them to verify they fail**

```bash
cd frontend && npx vitest run src/components/login-form.test.tsx
```
Expected: the first new test FAILS — no link is rendered.

- [ ] **Step 14: Add the link**

In `frontend/src/components/login-form.tsx`, add the imports:

```tsx
import Link from "next/link";
import { fetchCapabilities } from "@/lib/auth/api";
import { FORGOT_PASSWORD_ROUTE } from "@/lib/auth/session";
```

Add the state beside `accounts`:

```tsx
  // Hidden until the backend says a reset link could actually be sent. A
  // link to a page that says "check your inbox" when nothing will arrive is
  // worse than no link.
  const [canReset, setCanReset] = useState(false);
```

Add a second effect after the existing one:

```tsx
  useEffect(() => {
    let active = true;
    fetchCapabilities()
      .then((caps) => {
        if (active) setCanReset(caps.password_reset);
      })
      .catch(() => {
        // Unreachable backend: leave the link hidden. Login itself will
        // report the real error when the user tries.
      });
    return () => {
      active = false;
    };
  }, []);
```

Then, inside the `<form>`, immediately after the submit `<Button>`:

```tsx
            {canReset ? (
              <p className="text-center text-sm">
                <Link
                  href={FORGOT_PASSWORD_ROUTE}
                  className="text-muted-foreground underline-offset-4 hover:underline"
                >
                  Forgot password?
                </Link>
              </p>
            ) : null}
```

- [ ] **Step 15: Run everything**

```bash
cd frontend && npx vitest run src/components/login-form.test.tsx src/components/auth src/lib/auth
cd frontend && npm run typecheck && npm run lint && npm test
```
Expected: all green.

- [ ] **Step 16: Commit and tear down**

```bash
git add frontend/src/lib/auth frontend/src/lib/api/generated/schema.ts \
        frontend/src/components/auth frontend/src/app/forgot-password frontend/src/app/reset-password \
        frontend/src/components/login-form.tsx frontend/src/components/login-form.test.tsx
git commit -m "feat(login): forgot-password link and the two reset pages

The link appears only when the backend says a reset could actually be sent.
After a successful reset the user is sent to sign in rather than signed in: the
token arrived by email, and spending it to mint a session extends the trust
placed in that mailbox one step further than it needs to go.

Both pages are registered as public routes, or the guard bounces them to
/login before anyone can use them.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

```bash
docker rm -f megoopm-test megoopm-testdb && docker network rm megoopm-testnet
```

---

## Manual verification

With the stack up, SMTP configured, and `app_url` set to the address you will
open the link from:

- [ ] Sign in, then in a second browser sign in again. Reset the password from
      the first via the emailed link. The second browser's next request after
      its access token expires lands on `/login`.
- [ ] Request a reset for an address that does not exist. The page says
      exactly what it says for one that does.
- [ ] Request four resets for one address inside an hour. The fourth is
      refused with a message about waiting.
- [ ] Open the emailed link twice. The second time says the link is invalid or
      expired and offers to request a new one.
- [ ] Switch SMTP off in Settings. Reload `/login`: the "Forgot password?"
      link is gone.
- [ ] Stop Redis and request a reset. The page reports the service is
      temporarily unavailable, not "check your inbox".
- [ ] Check the received emails in a real client: the button in the reset
      email is the brand teal, the link works, and the changed-password notice
      contains no link at all.
