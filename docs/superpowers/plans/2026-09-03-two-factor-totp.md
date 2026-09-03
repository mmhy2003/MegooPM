# Two-Factor Authentication (TOTP) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A user enrols an authenticator app from their profile and signs in with a password and a code; admins can see who has it on and switch it off for someone who has lost everything.

**Architecture:** Three columns on `users` (encrypted secret, enabled-at, last accepted time-step for replay protection) and a `recovery_code` table of Argon2 hashes. Enrolment is two steps so a pending secret is never live until one code proves the app works. Login returns a five-minute `mfa` JWT instead of a session when 2FA is on; `/auth/mfa/verify` exchanges it plus a code for the real pair, rate-limited per user and per IP. Enable, disable and admin-disable all bump `token_version`.

**Tech Stack:** Everything from P1–P3, plus `pyotp` (declared; already in the image) and `qrcode.react` 4.2.0 on the frontend.

**Spec:** `docs/superpowers/specs/2026-09-03-two-factor-totp-design.md`

## Global Constraints

- **A pending secret never satisfies login.** `totp_enabled_at IS NULL` means 2FA is off, whatever `totp_secret_enc` holds.
- **Replay: a code whose time-step is not later than `totp_last_step` is refused**, even if otherwise correct.
- **Recovery codes are Argon2id hashes**, ten per set, from the alphabet `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`, shown once.
- **One refusal message** for wrong, expired, replayed and already-used codes, and for a stale `mfa_token`: *"That code is not valid."*
- **Enable, self-disable, admin-disable and regenerate all bump `token_version`.**
- **Self-disable requires a valid code. Admin-disable requires none and sends the email.**
- **`UserRead.totp_enabled` is a bool with a default**, so the generated type is optional and no existing `User` fixture breaks. Never the secret, never a code.
- **PyOTP matches RFC 6238**: with secret `GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ`, `at(59) == "287082"`, `at(1111111109) == "081804"`, `at(1234567890) == "005924"`. Measured in the image; the tests pin it.
- **Backend tests cannot run natively on Windows** (`fcntl`). Use the container recipe in Task 1, Step 2.
- Frontend commands run from `frontend/`.

## File Structure

**Backend**

| file | responsibility |
| --- | --- |
| `pyproject.toml` | declare `pyotp` |
| `app/models/user.py` | three columns, `totp_enabled` property |
| `app/models/recovery_code.py` | the hash rows |
| `alembic/versions/0028_totp.py` | migration |
| `app/services/totp.py` | secret, URI, verify with step tracking, recovery codes, enrol/disable |
| `app/core/security.py` | `mfa` token type, `create_mfa_token` |
| `app/services/rate_limit.py` | `check_mfa_verify` |
| `app/schemas/auth.py` | `MfaRequired`, `MfaVerifyRequest`, `MfaVerifyResponse` |
| `app/schemas/user.py` | `totp_enabled`; `TotpSetup`, `TotpCodeRequest`, `TotpCodes` |
| `app/api/routes/auth.py` | login union; `/mfa/verify` |
| `app/api/routes/users.py` | five `totp` routes |
| `app/services/mail/templates/totp_disabled.{html,txt}.j2` | the admin-disable notice |
| `tests/conftest.py` | `RecoveryCode` in the SQLite table list |

**Frontend**

| file | responsibility |
| --- | --- |
| `src/lib/auth/api.ts` | `LoginResult` union, `verifyMfa` |
| `src/lib/auth/context.tsx` | `login` returns a challenge or null; `verifyMfa`; shared `finishLogin` |
| `src/components/login-form.tsx` | the code step |
| `src/lib/api/resources/users.ts` | the totp calls |
| `src/components/profile/totp-card.tsx` | the four-state card |
| `src/components/profile/profile-view.tsx` | mount |
| `src/components/users/users-view.tsx` | 2FA column, admin action |

---

### Task 1: Storage and the TOTP service

The columns, the recovery-code table, the migration, the declared dependency,
and every piece of TOTP logic — verified against the RFC's own numbers.

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/app/models/user.py`
- Create: `backend/app/models/recovery_code.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/0028_totp.py`
- Create: `backend/app/services/totp.py`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/test_totp_service.py`

**Interfaces:**
- Consumes: `encrypt_secret`/`decrypt_secret` from `app.core.crypto`; `hash_password`/`verify_password` from `app.core.security`; `APP_NAME` from `app.services.mail.templates`.
- Produces (all in `app.services.totp`):
  - `TotpAlreadyEnabled`, `TotpNotPending`, `TotpNotEnabled` — exceptions
  - `INVALID_CODE_MESSAGE = "That code is not valid."`
  - `generate_secret() -> str`
  - `provisioning_uri(secret: str, email: str) -> str`
  - `verify_totp(secret: str, code: str, *, last_step: int | None, now: datetime | None = None) -> int | None` — the accepted step, or `None`.
  - `async start_enrolment(db, user) -> str` — raw secret; raises `TotpAlreadyEnabled`.
  - `async confirm_enrolment(db, user, code) -> list[str] | None` — the recovery codes, or `None` on a wrong code; raises `TotpNotPending`.
  - `async verify_code(db, user, code) -> bool` — TOTP or recovery, by shape; updates `last_step` or `used_at`; commits.
  - `async mint_recovery_codes(db, user) -> list[str]`
  - `async recovery_codes_remaining(db, user) -> int`
  - `async disable(db, user) -> None` — raises `TotpNotEnabled`.
  - `User.totp_enabled -> bool` property.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_totp_service.py`:

```python
"""TOTP against the RFC's numbers, and the service against the SQLite factory."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pyotp
import pytest
from app.core.crypto import decrypt_secret
from app.core.security import verify_password
from app.models.recovery_code import RecoveryCode
from app.models.user import User
from app.services import totp
from sqlalchemy import select

# RFC 6238 Appendix B: the ASCII secret "12345678901234567890", SHA-1 column,
# truncated to six digits.
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
RFC_VECTORS = [(59, "287082"), (1111111109, "081804"), (1234567890, "005924")]


def _at(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


def _next_code(secret: str) -> str:
    """The code for the *next* step. Enabling records the current step, so
    re-using the enrolment code would be a replay — which is the point."""
    return pyotp.TOTP(secret).at(int(time.time()) + 30)


# --- the arithmetic, against the standard ---------------------------------


@pytest.mark.parametrize(("ts", "code"), RFC_VECTORS)
def test_accepts_the_rfc_6238_vectors(ts: int, code: str) -> None:
    # Against the RFC, not against PyOTP's own output.
    assert totp.verify_totp(RFC_SECRET, code, last_step=None, now=_at(ts)) == ts // 30


def test_accepts_the_previous_step_for_clock_drift() -> None:
    code = pyotp.TOTP(RFC_SECRET).at(59)
    assert totp.verify_totp(RFC_SECRET, code, last_step=None, now=_at(89)) is not None


def test_refuses_two_steps_back() -> None:
    code = pyotp.TOTP(RFC_SECRET).at(59)
    assert totp.verify_totp(RFC_SECRET, code, last_step=None, now=_at(120)) is None


def test_refuses_a_replayed_code() -> None:
    # Ninety seconds of validity is ninety seconds for a shoulder-surfed code
    # to be typed again. The last accepted step closes that.
    code = pyotp.TOTP(RFC_SECRET).at(59)
    step = totp.verify_totp(RFC_SECRET, code, last_step=None, now=_at(59))
    assert step == 1
    assert totp.verify_totp(RFC_SECRET, code, last_step=step, now=_at(70)) is None


def test_refuses_a_wrong_code() -> None:
    assert totp.verify_totp(RFC_SECRET, "000000", last_step=None, now=_at(59)) is None


def test_refuses_garbage_without_raising() -> None:
    for junk in ("", "abc", "12345", "1234567", "28 7082"):
        assert totp.verify_totp(RFC_SECRET, junk, last_step=None, now=_at(59)) is None


def test_generated_secrets_are_distinct_base32() -> None:
    a, b = totp.generate_secret(), totp.generate_secret()
    assert a != b and len(a) == 32
    assert set(a) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")


def test_provisioning_uri_names_the_app_and_the_account() -> None:
    uri = totp.provisioning_uri(RFC_SECRET, "me@example.com")
    assert uri.startswith("otpauth://totp/MegooPM:me%40example.com?")
    assert "issuer=MegooPM" in uri
    assert f"secret={RFC_SECRET}" in uri


# --- enrolment ---------------------------------------------------------------


async def test_start_enrolment_stores_an_encrypted_pending_secret(
    session_factory, admin_user: User
) -> None:
    async with session_factory() as db:
        user = await db.get(User, admin_user.id)
        raw = await totp.start_enrolment(db, user)

    assert user.totp_enabled is False
    assert user.totp_enabled_at is None
    assert user.totp_secret_enc is not None
    assert raw not in user.totp_secret_enc
    assert decrypt_secret(user.totp_secret_enc) == raw


async def test_start_again_replaces_the_pending_secret(
    session_factory, admin_user: User
) -> None:
    async with session_factory() as db:
        user = await db.get(User, admin_user.id)
        first = await totp.start_enrolment(db, user)
        second = await totp.start_enrolment(db, user)
    assert first != second
    assert decrypt_secret(user.totp_secret_enc) == second


async def test_confirm_with_the_right_code_enables_and_mints_codes(
    session_factory, admin_user: User
) -> None:
    async with session_factory() as db:
        user = await db.get(User, admin_user.id)
        raw = await totp.start_enrolment(db, user)
        before = user.token_version
        codes = await totp.confirm_enrolment(db, user, pyotp.TOTP(raw).now())

        assert codes is not None and len(codes) == 10
        assert user.totp_enabled is True
        assert user.token_version == before + 1
        rows = (await db.execute(select(RecoveryCode))).scalars().all()
    assert len(rows) == 10
    # Hashed, never plaintext.
    for code in codes:
        assert not any(code in r.code_hash for r in rows)
    assert all(r.code_hash.startswith("$argon2") for r in rows)


async def test_confirm_with_a_wrong_code_leaves_it_pending(
    session_factory, admin_user: User
) -> None:
    async with session_factory() as db:
        user = await db.get(User, admin_user.id)
        await totp.start_enrolment(db, user)
        assert await totp.confirm_enrolment(db, user, "000000") is None
    assert user.totp_enabled is False
    assert user.totp_secret_enc is not None


async def test_confirm_without_a_pending_secret_is_refused(
    session_factory, admin_user: User
) -> None:
    async with session_factory() as db:
        user = await db.get(User, admin_user.id)
        with pytest.raises(totp.TotpNotPending):
            await totp.confirm_enrolment(db, user, "000000")


async def test_start_while_enabled_is_refused(session_factory, admin_user: User) -> None:
    async with session_factory() as db:
        user = await db.get(User, admin_user.id)
        raw = await totp.start_enrolment(db, user)
        await totp.confirm_enrolment(db, user, pyotp.TOTP(raw).now())
        with pytest.raises(totp.TotpAlreadyEnabled):
            await totp.start_enrolment(db, user)


# --- verifying, either kind ---------------------------------------------------


async def _enabled(session_factory, admin_user: User):
    db = session_factory()
    user = await db.get(User, admin_user.id)
    raw = await totp.start_enrolment(db, user)
    codes = await totp.confirm_enrolment(db, user, pyotp.TOTP(raw).now())
    return db, user, raw, codes


async def test_a_pending_secret_never_verifies(session_factory, admin_user: User) -> None:
    async with session_factory() as db:
        user = await db.get(User, admin_user.id)
        raw = await totp.start_enrolment(db, user)
        assert await totp.verify_code(db, user, pyotp.TOTP(raw).now()) is False


async def test_verify_accepts_a_current_totp_once(session_factory, admin_user: User) -> None:
    db, user, raw, _ = await _enabled(session_factory, admin_user)
    async with db:
        code = _next_code(raw)
        assert await totp.verify_code(db, user, code) is True
        assert await totp.verify_code(db, user, code) is False  # replay


async def test_verify_accepts_each_recovery_code_once(
    session_factory, admin_user: User
) -> None:
    db, user, _, codes = await _enabled(session_factory, admin_user)
    async with db:
        assert await totp.verify_code(db, user, codes[0]) is True
        assert await totp.verify_code(db, user, codes[0]) is False
        assert await totp.recovery_codes_remaining(db, user) == 9


async def test_recovery_codes_are_case_and_dash_insensitive(
    session_factory, admin_user: User
) -> None:
    db, user, _, codes = await _enabled(session_factory, admin_user)
    async with db:
        typed = codes[1].lower().replace("-", " ")
        assert await totp.verify_code(db, user, typed) is True


async def test_regenerating_kills_the_old_set(session_factory, admin_user: User) -> None:
    db, user, _, old = await _enabled(session_factory, admin_user)
    async with db:
        new = await totp.mint_recovery_codes(db, user)
        assert set(new).isdisjoint(old)
        assert await totp.verify_code(db, user, old[0]) is False
        assert await totp.verify_code(db, user, new[0]) is True


async def test_codes_avoid_ambiguous_characters() -> None:
    from app.services.totp import _random_recovery_code

    for _ in range(50):
        code = _random_recovery_code()
        assert len(code) == 11 and code[5] == "-"
        assert not set(code.replace("-", "")) & set("0O1I")


# --- disabling ---------------------------------------------------------------


async def test_disable_clears_everything_and_bumps_the_version(
    session_factory, admin_user: User
) -> None:
    db, user, _, _ = await _enabled(session_factory, admin_user)
    async with db:
        before = user.token_version
        await totp.disable(db, user)
        rows = (await db.execute(select(RecoveryCode))).scalars().all()
    assert user.totp_enabled is False
    assert user.totp_secret_enc is None
    assert user.totp_last_step is None
    assert user.token_version == before + 1
    assert rows == []


async def test_disable_when_off_is_refused(session_factory, admin_user: User) -> None:
    async with session_factory() as db:
        user = await db.get(User, admin_user.id)
        with pytest.raises(totp.TotpNotEnabled):
            await totp.disable(db, user)


async def test_recovery_hashes_verify_with_the_password_hasher(
    session_factory, admin_user: User
) -> None:
    # The same Argon2id parameters as passwords — one place to tune.
    db, user, _, codes = await _enabled(session_factory, admin_user)
    async with db:
        rows = (await db.execute(select(RecoveryCode))).scalars().all()
    assert any(verify_password(codes[0].replace("-", ""), r.code_hash) for r in rows)
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
docker exec megoopm-test python -m pytest tests/test_totp_service.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.recovery_code'`.

- [ ] **Step 3: Declare the dependency**

In `backend/pyproject.toml`, after `"argon2-cffi>=23.1",`:

```toml
    # TOTP (RFC 6238) for two-factor authentication. Already in the image as a
    # transitive dependency; declared so the feature does not depend on a
    # package nobody chose.
    "pyotp>=2.9",
```

- [ ] **Step 4: The columns, the property, and the table**

In `backend/app/models/user.py`, after `invited_at`:

```python
    # --- Two-factor authentication ---------------------------------------
    # Fernet token (app.core.crypto), never plaintext. A secret with no
    # enabled_at is a *pending* enrolment: shown, but never proven to work.
    # Login ignores it entirely, so an abandoned setup locks nobody out.
    totp_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_enabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The time-step of the last code accepted. A code whose step is not later
    # is refused even if correct: a code is valid for up to ninety seconds
    # under the drift window, which is ninety seconds to replay one seen over
    # a shoulder. PyOTP does not track this; the service does.
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    @property
    def totp_enabled(self) -> bool:
        """Whether a second factor is required at login."""
        return self.totp_enabled_at is not None
```

Add `BigInteger, Text` to the `sqlalchemy` import.

Create `backend/app/models/recovery_code.py`:

```python
"""One-time codes for signing in without the authenticator app.

Argon2id, not SHA-256: a recovery code is ten characters — about fifty bits.
That survives a rate-limited guess over the network and does not survive an
offline attack on a fast hash if this table leaks.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin


class RecoveryCode(IdMixin, Base):
    """One code. ``used_at`` set means spent."""

    __tablename__ = "recovery_code"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = ["RecoveryCode"]
```

Register it in `backend/app/models/__init__.py` (alphabetical import, `"RecoveryCode"` in `__all__`), and add `RecoveryCode.__table__` to the `tables=[…]` list in `backend/tests/conftest.py` with its import.

- [ ] **Step 5: The migration**

Create `backend/alembic/versions/0028_totp.py`:

```python
"""Two-factor authentication: TOTP columns on users; recovery_code table

Revision ID: 0028_totp
Revises: 0027_invitations
Create Date: 2026-09-03 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_totp"
down_revision: str | None = "0027_invitations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_secret_enc", sa.Text(), nullable=True))
    op.add_column(
        "users", sa.Column("totp_enabled_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("users", sa.Column("totp_last_step", sa.BigInteger(), nullable=True))
    op.create_table(
        "recovery_code",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("code_hash", sa.String(length=255), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_recovery_code_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recovery_code")),
    )
    op.create_index(op.f("ix_recovery_code_user_id"), "recovery_code", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_recovery_code_user_id"), table_name="recovery_code")
    op.drop_table("recovery_code")
    op.drop_column("users", "totp_last_step")
    op.drop_column("users", "totp_enabled_at")
    op.drop_column("users", "totp_secret_enc")
```

- [ ] **Step 6: The service**

Create `backend/app/services/totp.py`:

```python
"""Time-based one-time passwords and their recovery codes.

The arithmetic is PyOTP's; the two things it does not do live here: replay
protection by last-accepted step, and the pending/enabled distinction that
keeps an unproven secret from ever satisfying a login.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

import pyotp
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.security import hash_password, verify_password
from app.models.recovery_code import RecoveryCode
from app.models.user import User
from app.services.mail.templates import APP_NAME

#: Shown to the user for every kind of refusal. Wrong, expired, replayed and
#: already-used are indistinguishable on purpose.
INVALID_CODE_MESSAGE = "That code is not valid."

RECOVERY_CODE_COUNT = 10
#: No 0/O or 1/I: these are read off a printout and typed by hand.
RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

_STEP_SECONDS = 30
#: One step either side, for clock drift between the server and a phone.
_DRIFT_WINDOW = 1


class TotpAlreadyEnabled(Exception):
    """Setup was requested while 2FA is already on. Turn it off first."""


class TotpNotPending(Exception):
    """Confirmation was requested with no pending secret to confirm."""


class TotpNotEnabled(Exception):
    """Disable was requested while 2FA is already off."""


# --- pure -------------------------------------------------------------------


def generate_secret() -> str:
    """A fresh base32 secret, 160 bits."""
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    """The ``otpauth://`` URI an authenticator app enrols from."""
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=APP_NAME)


def _step_of(when: datetime) -> int:
    return int(when.timestamp()) // _STEP_SECONDS


def verify_totp(
    secret: str, code: str, *, last_step: int | None, now: datetime | None = None
) -> int | None:
    """Return the time-step ``code`` matched, or ``None``.

    Accepts the current step and one either side. Refuses any step not later
    than ``last_step`` — that is the replay guard.
    """
    digits = code.strip().replace(" ", "")
    if len(digits) != 6 or not digits.isdigit():
        return None
    when = now or datetime.now(UTC)
    current = _step_of(when)
    totp = pyotp.TOTP(secret)
    for offset in range(-_DRIFT_WINDOW, _DRIFT_WINDOW + 1):
        step = current + offset
        if last_step is not None and step <= last_step:
            continue
        if secrets.compare_digest(totp.at(step * _STEP_SECONDS), digits):
            return step
    return None


def _random_recovery_code() -> str:
    body = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(10))
    return f"{body[:5]}-{body[5:]}"


def _normalise_recovery(code: str) -> str:
    return code.strip().upper().replace("-", "").replace(" ", "")


# --- enrolment -----------------------------------------------------------------


async def start_enrolment(db: AsyncSession, user: User) -> str:
    """Generate and store a *pending* secret; return it raw for display.

    Calling again replaces the pending secret. Refused while 2FA is on.
    """
    if user.totp_enabled:
        raise TotpAlreadyEnabled()
    raw = generate_secret()
    user.totp_secret_enc = encrypt_secret(raw)
    user.totp_last_step = None
    await db.commit()
    return raw


async def confirm_enrolment(db: AsyncSession, user: User, code: str) -> list[str] | None:
    """Prove the app works, then enable. Returns the recovery codes, once.

    A wrong code returns ``None`` and leaves everything pending. Enabling
    without proof is how people lock themselves out of the thing that was
    supposed to protect them.
    """
    if user.totp_enabled or not user.totp_secret_enc:
        raise TotpNotPending()
    secret = decrypt_secret(user.totp_secret_enc)
    step = verify_totp(secret, code, last_step=None)
    if step is None:
        return None
    user.totp_enabled_at = datetime.now(UTC)
    user.totp_last_step = step
    # A change to how the account is protected ends the sessions opened
    # under the old rules.
    user.token_version += 1
    codes = await _replace_recovery_codes(db, user)
    await db.commit()
    return codes


async def _replace_recovery_codes(db: AsyncSession, user: User) -> list[str]:
    await db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    codes = [_random_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
    for code in codes:
        db.add(RecoveryCode(user_id=user.id, code_hash=hash_password(_normalise_recovery(code))))
    return codes


async def mint_recovery_codes(db: AsyncSession, user: User) -> list[str]:
    """Replace every recovery code with ten new ones. Commits."""
    if not user.totp_enabled:
        raise TotpNotEnabled()
    codes = await _replace_recovery_codes(db, user)
    user.token_version += 1
    await db.commit()
    return codes


async def recovery_codes_remaining(db: AsyncSession, user: User) -> int:
    rows = (
        await db.execute(
            select(RecoveryCode).where(
                RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None)
            )
        )
    ).scalars().all()
    return len(rows)


# --- verifying ------------------------------------------------------------------


async def verify_code(db: AsyncSession, user: User, code: str) -> bool:
    """Accept a TOTP or a recovery code, by shape. Commits on success.

    Six digits is TOTP; anything else is treated as a recovery code. A
    pending secret never verifies: 2FA is off until it is proven.
    """
    if not user.totp_enabled or not user.totp_secret_enc:
        return False

    stripped = code.strip().replace(" ", "")
    if len(stripped) == 6 and stripped.isdigit():
        step = verify_totp(decrypt_secret(user.totp_secret_enc), stripped, last_step=user.totp_last_step)
        if step is None:
            return False
        user.totp_last_step = step
        await db.commit()
        return True

    wanted = _normalise_recovery(code)
    unused = (
        await db.execute(
            select(RecoveryCode).where(
                RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None)
            )
        )
    ).scalars().all()
    # Deliberately slow — Argon2 per candidate — and only on the recovery
    # path, which is rare by construction.
    for row in unused:
        if verify_password(wanted, row.code_hash):
            row.used_at = datetime.now(UTC)
            await db.commit()
            return True
    return False


# --- disabling ---------------------------------------------------------------


async def disable(db: AsyncSession, user: User) -> None:
    """Clear the secret, delete the codes, end sessions. Commits."""
    if not user.totp_enabled:
        raise TotpNotEnabled()
    user.totp_secret_enc = None
    user.totp_enabled_at = None
    user.totp_last_step = None
    user.token_version += 1
    await db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    await db.commit()


__all__ = [
    "INVALID_CODE_MESSAGE",
    "RECOVERY_CODE_COUNT",
    "TotpAlreadyEnabled",
    "TotpNotEnabled",
    "TotpNotPending",
    "confirm_enrolment",
    "disable",
    "generate_secret",
    "mint_recovery_codes",
    "provisioning_uri",
    "recovery_codes_remaining",
    "start_enrolment",
    "verify_code",
    "verify_totp",
]
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
docker exec megoopm-test python -m pytest tests/test_totp_service.py tests/test_auth.py -p no:cacheprovider -p no:warnings
```
Expected: PASS. (`test_auth.py` is the canary for the conftest change.)

- [ ] **Step 8: Commit**

```bash
git add backend/pyproject.toml backend/app/models/user.py backend/app/models/recovery_code.py \
        backend/app/models/__init__.py backend/alembic/versions/0028_totp.py \
        backend/app/services/totp.py backend/tests/conftest.py backend/tests/test_totp_service.py
git commit -m "feat(auth): TOTP secrets, recovery codes, and the service behind them

Verified against RFC 6238's published vectors rather than PyOTP's own output.
Replay is closed by recording the last accepted time-step, which PyOTP does
not do. A pending secret never verifies: 2FA is off until one code proves the
app works.

Recovery codes are Argon2id: ten characters survives a rate-limited guess and
not an offline attack on a fast hash.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: The `mfa` token and its rate limit

A third token type for the five minutes between a correct password and a
correct code, and the limiter that makes those five minutes unbrute-forceable.

**Files:**
- Modify: `backend/app/core/security.py`
- Modify: `backend/app/services/rate_limit.py`
- Test: `backend/tests/test_security.py` (append)
- Test: `backend/tests/test_rate_limit.py` (append)

**Interfaces:**
- Produces:
  - `TokenType = Literal["access", "refresh", "mfa"]`
  - `create_mfa_token(subject, *, token_version: int) -> str` — five minutes.
  - `MFA_ATTEMPT_LIMIT = 10`, `MFA_WINDOW_S = 300`
  - `check_mfa_verify(*, user_id: int, ip: str, client=None) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_security.py`:

```python
# --- the mfa token ------------------------------------------------------------


def test_mfa_token_is_its_own_type() -> None:
    from app.core.security import create_mfa_token, decode_token

    token = create_mfa_token(42, token_version=3)
    payload = decode_token(token, expected_type="mfa")
    assert payload["sub"] == "42"
    assert payload["tv"] == 3


def test_mfa_token_cannot_be_used_as_an_access_token() -> None:
    # A challenge token must not open the door it exists to guard.
    import jwt
    import pytest
    from app.core.security import create_mfa_token, decode_token

    token = create_mfa_token(42, token_version=0)
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token, expected_type="access")
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token, expected_type="refresh")


def test_mfa_token_lives_five_minutes() -> None:
    from datetime import UTC, datetime

    from app.core.security import create_mfa_token, decode_token

    payload = decode_token(create_mfa_token(1, token_version=0), expected_type="mfa")
    ttl = payload["exp"] - int(datetime.now(UTC).timestamp())
    assert 290 <= ttl <= 300
```

Append to `backend/tests/test_rate_limit.py`:

```python
async def test_mfa_verify_is_limited_per_user() -> None:
    # Ten attempts per five minutes per user: a six-digit space with a
    # three-step window is an afternoon's work without this.
    client = FakeRedis()
    for _ in range(MFA_ATTEMPT_LIMIT):
        await check_mfa_verify(user_id=7, ip="1.1.1.1", client=client)
    with pytest.raises(RateLimited):
        await check_mfa_verify(user_id=7, ip="1.1.1.1", client=client)


async def test_mfa_verify_limits_are_per_user_not_global() -> None:
    client = FakeRedis()
    for _ in range(MFA_ATTEMPT_LIMIT):
        await check_mfa_verify(user_id=7, ip="1.1.1.1", client=client)
    # A different user from a different address is unaffected.
    await check_mfa_verify(user_id=8, ip="2.2.2.2", client=client)
```

Add `MFA_ATTEMPT_LIMIT` and `check_mfa_verify` to that file's import from
`app.services.rate_limit`.

- [ ] **Step 2: Run them to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_security.py tests/test_rate_limit.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — `ImportError: cannot import name 'create_mfa_token'` / `'MFA_ATTEMPT_LIMIT'`.

- [ ] **Step 3: The token**

In `backend/app/core/security.py`:

```python
TokenType = Literal["access", "refresh", "mfa"]
```

and after `create_refresh_token`:

```python
#: Long enough to find the phone; short enough that a stolen one is nearly
#: worthless. Attempts against it are rate-limited on top.
MFA_TOKEN_MINUTES = 5


def create_mfa_token(subject: str | int, *, token_version: int) -> str:
    """The token between a correct password and a correct code.

    Stateless on purpose: replaying it earns only another rate-limited
    challenge attempt, and a ``token_version`` bump — from a password or a
    2FA change — makes it dead on arrival.
    """
    return _create_token(
        subject=str(subject),
        token_type="mfa",
        expires_delta=timedelta(minutes=MFA_TOKEN_MINUTES),
        extra_claims={"tv": token_version},
    )
```

- [ ] **Step 4: The limit**

In `backend/app/services/rate_limit.py`, after `RESET_WINDOW_S`:

```python
MFA_ATTEMPT_LIMIT = 10
MFA_WINDOW_S = 300
```

and after `check_password_reset_redeem`:

```python
async def check_mfa_verify(
    *, user_id: int, ip: str, client: aioredis.Redis | None = None
) -> None:
    """Both limits on the code-entry step: per user (from the mfa token's
    subject) and per IP."""
    own = client is None
    redis_client = client if client is not None else _client()
    try:
        await hit(
            redis_client,
            f"{_PREFIX}:mfa:user:{user_id}",
            limit=MFA_ATTEMPT_LIMIT,
            window_s=MFA_WINDOW_S,
        )
        await hit(
            redis_client,
            f"{_PREFIX}:mfa:ip:{ip}",
            limit=RESET_IP_LIMIT,
            window_s=RESET_WINDOW_S,
        )
    finally:
        if own:
            await redis_client.aclose()
```

Add `"MFA_ATTEMPT_LIMIT"`, `"MFA_WINDOW_S"`, `"check_mfa_verify"` to `__all__`.

- [ ] **Step 5: Run them to verify they pass, then commit**

```bash
docker exec megoopm-test python -m pytest tests/test_security.py tests/test_rate_limit.py -p no:cacheprovider -p no:warnings
git add backend/app/core/security.py backend/app/services/rate_limit.py \
        backend/tests/test_security.py backend/tests/test_rate_limit.py
git commit -m "feat(auth): the mfa token and its rate limit

A third token type for the five minutes between a correct password and a
correct code. Stateless: replaying it earns only another rate-limited attempt,
and a token_version bump makes it dead on arrival.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Login becomes a challenge

The login route returns a union; a new route exchanges the challenge plus a
code for the real pair.

**Files:**
- Modify: `backend/app/schemas/auth.py`
- Modify: `backend/app/api/routes/auth.py`
- Test: `backend/tests/test_mfa_login_api.py`

**Interfaces:**
- Consumes: Tasks 1 and 2; `client_ip`; `_limit`, `_unavailable`, `_issue_tokens` in `routes/auth.py`.
- Produces:
  - `MfaRequired(mfa_required: Literal[True], mfa_token: str)`
  - `MfaVerifyRequest(mfa_token: str, code: str)`
  - `MfaVerifyResponse(TokenPair)` + `recovery_codes_remaining: int | None`
  - `POST /auth/login` → `TokenPair | MfaRequired`
  - `POST /auth/mfa/verify` → `MfaVerifyResponse`; 401; 429; 503

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_mfa_login_api.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_mfa_login_api.py -p no:cacheprovider -p no:warnings
```
Expected: the `enabled` fixture works (Task 1), then FAIL — login returns
tokens where a challenge is expected, and 404 on `/mfa/verify`.

- [ ] **Step 3: The schemas**

In `backend/app/schemas/auth.py`, add `Literal` to the `typing` import (or
`from typing import Literal`), then after `TokenPair`:

```python
class MfaRequired(BaseModel):
    """What ``POST /auth/login`` returns when a second factor is needed.

    ``mfa_required`` is a literal so the frontend can discriminate the union
    without inspecting which keys are present.
    """

    mfa_required: Literal[True] = True
    mfa_token: str


class MfaVerifyRequest(BaseModel):
    """Body for ``POST /auth/mfa/verify``."""

    mfa_token: str = Field(min_length=1)
    code: str = Field(min_length=1, max_length=32)


class MfaVerifyResponse(TokenPair):
    """The real pair, plus how many recovery codes are left when one was used."""

    recovery_codes_remaining: int | None = None
```

Add the three names to `__all__`.

- [ ] **Step 4: The routes**

In `backend/app/api/routes/auth.py`, extend the imports:

```python
from app.core.security import (
    create_access_token,
    create_mfa_token,
    create_refresh_token,
    decode_token,
)
from app.schemas.auth import (
    AcceptInviteRequest,
    AuthCapabilities,
    ForgotPasswordRequest,
    LoginRequest,
    MfaRequired,
    MfaVerifyRequest,
    MfaVerifyResponse,
    NeutralResponse,
    RefreshRequest,
    ResetPasswordRequest,
    TokenPair,
)
from app.services import auth_tokens, rate_limit, totp
```

Replace the `login` route:

```python
@router.post("/login", response_model=TokenPair | MfaRequired)
async def login(body: LoginRequest, db: SessionDep) -> TokenPair | MfaRequired:
    """Authenticate with email + password.

    Returns a token pair — or, for a user with 2FA on, a five-minute
    ``mfa_token`` to present with a code at ``/auth/mfa/verify``. A wrong
    password is 401 either way: the challenge must not leak that the password
    was right.
    """
    user = await user_service.authenticate(db, email=body.email, password=body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user.totp_enabled:
        return MfaRequired(mfa_token=create_mfa_token(user.id, token_version=user.token_version))
    return _issue_tokens(user)
```

Then add, after `accept_invite`:

```python
@router.post("/mfa/verify", response_model=MfaVerifyResponse)
async def mfa_verify(body: MfaVerifyRequest, request: Request, db: SessionDep) -> MfaVerifyResponse:
    """Exchange a challenge token plus a code for the real token pair.

    One message for every refusal — bad token, expired token, wrong code,
    replayed code, spent recovery code. Any distinction tells an attacker
    which part they got right.
    """
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=totp.INVALID_CODE_MESSAGE,
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(body.mfa_token, expected_type="mfa")
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise invalid from None

    try:
        await rate_limit.check_mfa_verify(user_id=user_id, ip=client_ip(request))
    except rate_limit.RateLimited as exc:
        raise _limit(exc) from None
    except rate_limit.RateLimitUnavailable:
        raise _unavailable() from None

    user = await user_service.get_by_id(db, user_id)
    if user is None or not user.is_active or payload.get("tv") != user.token_version:
        raise invalid
    if not await totp.verify_code(db, user, body.code):
        raise invalid

    # Only a recovery code changes the remaining count; a TOTP reports None
    # so the client does not nag after every ordinary sign-in.
    stripped = body.code.strip().replace(" ", "")
    used_recovery = not (len(stripped) == 6 and stripped.isdigit())
    remaining = await totp.recovery_codes_remaining(db, user) if used_recovery else None
    pair = _issue_tokens(user)
    return MfaVerifyResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        recovery_codes_remaining=remaining,
    )
```

- [ ] **Step 5: Run them to verify they pass, then commit**

```bash
docker exec megoopm-test python -m pytest tests/test_mfa_login_api.py tests/test_auth.py tests/test_password_reset_api.py -p no:cacheprovider -p no:warnings
git add backend/app/schemas/auth.py backend/app/api/routes/auth.py backend/tests/test_mfa_login_api.py
git commit -m "feat(auth): login becomes a challenge when 2FA is on

A five-minute mfa token instead of a session; /auth/mfa/verify exchanges it
plus a code for the real pair. One message for every refusal — bad token,
expired token, wrong code, spent recovery code — because any distinction tells
an attacker which part they got right.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The user-facing and admin routes

Enrol, confirm, regenerate, disable — and the admin backstop with its email.

**Files:**
- Modify: `backend/app/schemas/user.py`
- Modify: `backend/app/api/routes/users.py`
- Create: `backend/app/services/mail/templates/totp_disabled.html.j2`, `.txt.j2`
- Modify: `backend/openapi.json` (regenerated)
- Test: `backend/tests/test_totp_api.py`
- Test: `backend/tests/test_mail_templates.py` (append)

**Interfaces:**
- Produces:
  - `UserRead.totp_enabled: bool = False`
  - `TotpSetup(secret, otpauth_uri)`, `TotpCodeRequest(code)`, `TotpCodes(codes: list[str])`
  - `POST /users/me/totp/setup` → `TotpSetup`; 409 if enabled
  - `POST /users/me/totp/enable {code}` → `TotpCodes`; 400 wrong; 409 nothing pending
  - `POST /users/me/totp/disable {code}` → 204; 400 wrong; 409 not enabled
  - `POST /users/me/totp/recovery-codes {code}` → `TotpCodes`
  - `POST /users/{id}/totp/disable` → 204 (admin); 409 not enabled; sends email
  - template `totp_disabled` with `app_name`, `admin_name`

- [ ] **Step 1: Write the failing template tests**

Append to `backend/tests/test_mail_templates.py`:

```python
# --- 2FA disabled by an administrator -------------------------------------


def test_totp_disabled_notice_names_the_admin_and_has_no_link() -> None:
    # If the user did not ask for this, this email is how they find out — so
    # it says who, and it carries nothing clickable.
    email = render("totp_disabled", subject="2FA off", app_name="MegooPM", admin_name="Sara Ali")
    assert "Sara Ali" in email.text
    assert "href=" not in email.html
    assert "http" not in email.text
```

- [ ] **Step 2: Write the failing route tests**

Create `backend/tests/test_totp_api.py`:

```python
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
    other = await db_client.post(LOGIN, json={"email": member_user.email, "password": "memberpass123"})
    await _enable(db_client, member_token)
    resp = await db_client.post(REFRESH, json={"refresh_token": other.json()["refresh_token"]})
    assert resp.status_code == 401


async def test_enable_is_audited(
    db_client: AsyncClient, member_token: str, member_user: User, session_factory
) -> None:
    await _enable(db_client, member_token)
    async with session_factory() as db:
        rows = (await db.execute(select(AuditLog).where(AuditLog.object_id == member_user.id))).scalars().all()
    assert any(r.meta.get("totp") == "enabled" for r in rows)


# --- recovery codes ---------------------------------------------------------


async def test_regenerate_requires_a_valid_code(
    db_client: AsyncClient, member_token: str
) -> None:
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
    db_client: AsyncClient, admin_token: str, admin_user: User, member_token: str, member_user: User, mail
) -> None:
    await _enable(db_client, member_token)

    resp = await db_client.post(f"/api/v1/users/{member_user.id}/totp/disable", headers=_auth(admin_token))

    assert resp.status_code == 204, resp.text
    # Their next sign-in has no code step.
    login = await db_client.post(LOGIN, json={"email": member_user.email, "password": "memberpass123"})
    assert "access_token" in login.json()

    assert len(mail.calls) == 1
    assert mail.calls[0]["to"] == member_user.email
    assert mail.calls[0]["template"] == "totp_disabled"
    assert mail.calls[0]["context"]["admin_name"] == "Admin User"


async def test_admin_disable_is_admin_only(
    db_client: AsyncClient, member_token: str, member_user: User, admin_user: User, mail
) -> None:
    resp = await db_client.post(f"/api/v1/users/{admin_user.id}/totp/disable", headers=_auth(member_token))
    assert resp.status_code == 403


async def test_admin_disable_when_off_is_409(
    db_client: AsyncClient, admin_token: str, member_user: User, mail
) -> None:
    resp = await db_client.post(f"/api/v1/users/{member_user.id}/totp/disable", headers=_auth(admin_token))
    assert resp.status_code == 409
    assert mail.calls == []


async def test_admin_disable_is_audited_naming_the_admin(
    db_client: AsyncClient, admin_token: str, admin_user: User, member_token: str, member_user: User, mail, session_factory
) -> None:
    await _enable(db_client, member_token)
    await db_client.post(f"/api/v1/users/{member_user.id}/totp/disable", headers=_auth(admin_token))
    async with session_factory() as db:
        rows = (await db.execute(select(AuditLog).where(AuditLog.object_id == member_user.id))).scalars().all()
    assert any(r.meta.get("totp") == "disabled_by_admin" and r.actor == admin_user.email for r in rows)
```

- [ ] **Step 3: Run them to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_totp_api.py tests/test_mail_templates.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — 404 on every `totp` route, `TemplateNotFound` for the notice.

- [ ] **Step 4: The template**

Create `backend/app/services/mail/templates/totp_disabled.html.j2`:

```jinja
{% extends "base.html.j2" %}
{% block subject_text %}Two-factor authentication was turned off{% endblock %}
{% block body %}
<p class="m-accent" style="margin:0 0 16px 0;font-size:18px;font-weight:600;
                           color:{{ light.primary }};">Two-factor authentication was turned off</p>
<p style="margin:0 0 16px 0;">
  {{ admin_name }}, an administrator of {{ app_name }}, turned off two-factor
  authentication on your account. Every other session was signed out. You can
  turn it back on from your profile.
</p>
<p class="m-muted" style="margin:0;color:{{ light.muted_foreground }};font-size:13px;">
  If you did not ask for this, contact an administrator of {{ app_name }}
  straight away.
</p>
{% endblock %}
```

Create `backend/app/services/mail/templates/totp_disabled.txt.j2`:

```jinja
Two-factor authentication was turned off

{{ admin_name }}, an administrator of {{ app_name }}, turned off two-factor
authentication on your account. Every other session was signed out. You can
turn it back on from your profile.

If you did not ask for this, contact an administrator of {{ app_name }}
straight away.

--
Sent by {{ app_name }}.
```

- [ ] **Step 5: The schemas**

In `backend/app/schemas/user.py`, after `UserInvite`:

```python
class TotpSetup(BaseModel):
    """What the profile page needs to enrol an authenticator app."""

    secret: str
    otpauth_uri: str


class TotpCodeRequest(BaseModel):
    """A TOTP or recovery code, wherever one is required."""

    code: str = Field(min_length=1, max_length=32)


class TotpCodes(BaseModel):
    """Recovery codes. Returned exactly once; never retrievable."""

    codes: list[str]
```

and in `UserRead`, after `invited_at`:

```python
    # Derived from totp_enabled_at via the model property. Never the secret,
    # never a code, never last_step.
    totp_enabled: bool = False
```

Add the three names to `__all__`.

- [ ] **Step 6: The routes**

In `backend/app/api/routes/users.py`, extend the schema import with
`TotpCodeRequest, TotpCodes, TotpSetup` and add `from app.services import totp`.
Then, in the `--- self ---` section (before the `/{user_id}` routes):

```python
def _invalid_code() -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=totp.INVALID_CODE_MESSAGE)


@router.post("/me/totp/setup", response_model=TotpSetup)
async def totp_setup(current_user: CurrentUser, db: SessionDep) -> TotpSetup:
    """Start enrolling an authenticator app. 2FA stays off until confirmed."""
    try:
        secret = await totp.start_enrolment(db, current_user)
    except totp.TotpAlreadyEnabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Two-factor authentication is already on. Turn it off first.",
        ) from None
    return TotpSetup(secret=secret, otpauth_uri=totp.provisioning_uri(secret, current_user.email))


@router.post("/me/totp/enable", response_model=TotpCodes)
async def totp_enable(body: TotpCodeRequest, current_user: CurrentUser, db: SessionDep) -> TotpCodes:
    """Prove the app works, then turn 2FA on. Returns the recovery codes once."""
    try:
        codes = await totp.confirm_enrolment(db, current_user, body.code)
    except totp.TotpNotPending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Start setup before confirming it."
        ) from None
    if codes is None:
        raise _invalid_code()
    await _audit(db, actor=current_user, action=AuditAction.update, object_id=current_user.id, meta={"totp": "enabled"})
    return TotpCodes(codes=codes)


@router.post("/me/totp/disable", status_code=status.HTTP_204_NO_CONTENT)
async def totp_disable(body: TotpCodeRequest, current_user: CurrentUser, db: SessionDep) -> None:
    """Turn 2FA off. A valid code is required: a stolen session must not be
    able to strip the second factor."""
    if not current_user.totp_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Two-factor authentication is not on.")
    if not await totp.verify_code(db, current_user, body.code):
        raise _invalid_code()
    await totp.disable(db, current_user)
    await _audit(db, actor=current_user, action=AuditAction.update, object_id=current_user.id, meta={"totp": "disabled"})


@router.post("/me/totp/recovery-codes", response_model=TotpCodes)
async def totp_regenerate(body: TotpCodeRequest, current_user: CurrentUser, db: SessionDep) -> TotpCodes:
    """Replace every recovery code. A valid code is required."""
    if not current_user.totp_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Two-factor authentication is not on.")
    if not await totp.verify_code(db, current_user, body.code):
        raise _invalid_code()
    codes = await totp.mint_recovery_codes(db, current_user)
    await _audit(db, actor=current_user, action=AuditAction.update, object_id=current_user.id, meta={"totp": "codes_regenerated"})
    return TotpCodes(codes=codes)
```

And with the `/{user_id}` routes:

```python
@router.post("/{user_id}/totp/disable", status_code=status.HTTP_204_NO_CONTENT)
async def admin_totp_disable(user_id: int, admin: AdminUser, db: SessionDep) -> None:
    """Turn off another user's 2FA. Admin-only; no code — this is the
    lost-phone backstop. The user is told by email, naming the admin."""
    user = await _get_or_404(db, user_id)
    try:
        await totp.disable(db, user)
    except totp.TotpNotEnabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Two-factor authentication is not on for that user.") from None
    await _audit(db, actor=admin, action=AuditAction.update, object_id=user.id, meta={"totp": "disabled_by_admin"})
    send_email_task.delay(
        to=user.email,
        template="totp_disabled",
        subject=f"Two-factor authentication was turned off on your {APP_NAME} account",
        context={"app_name": APP_NAME, "admin_name": admin.full_name.strip() or admin.email},
    )
```

- [ ] **Step 7: Run everything, regenerate, commit**

```bash
docker exec megoopm-test python -m pytest tests/test_totp_api.py tests/test_mail_templates.py tests/test_users_management.py tests/test_users_rbac.py -p no:cacheprovider -p no:warnings
docker exec megoopm-test python -m scripts.export_openapi
docker exec megoopm-test ruff check app tests
docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings
git add backend/app/schemas/user.py backend/app/api/routes/users.py \
        backend/app/services/mail/templates/totp_disabled.html.j2 backend/app/services/mail/templates/totp_disabled.txt.j2 \
        backend/tests/test_totp_api.py backend/tests/test_mail_templates.py backend/openapi.json
git commit -m "feat(users): enrol, confirm, regenerate, disable, and the admin backstop

Self-disable requires a valid code — the one place 'the session is the proof'
stops, because 2FA exists for the case where the session was not the user.
Admin disable needs none; that is the lost-phone backstop, and the user is
emailed, naming the admin.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: The login form's code step

The auth context learns to return a challenge instead of signing in; the form
grows a second step.

**Files:**
- Modify: `frontend/src/lib/api/generated/schema.ts` (regenerated)
- Modify: `frontend/src/lib/auth/api.ts`
- Modify: `frontend/src/lib/auth/context.tsx`
- Modify: `frontend/src/components/login-form.tsx`
- Test: `frontend/src/components/login-form.test.tsx` (append + mock change)

**Interfaces:**
- Produces:
  - `type LoginResult = TokenPair | MfaRequired`; `login()` returns it
  - `verifyMfa(mfaToken: string, code: string): Promise<MfaVerifyResponse>`
  - `AuthContextValue.login: (email, password) => Promise<{ mfaToken: string } | null>`
  - `AuthContextValue.verifyMfa: (mfaToken, code) => Promise<{ recoveryCodesRemaining: number | null }>`

- [ ] **Step 1: Regenerate the types**

```bash
cd frontend && npm run gen:api
```

- [ ] **Step 2: Write the failing tests**

In `frontend/src/components/login-form.test.tsx`, change the context mock:

```tsx
const login = vi.fn();
const verifyMfa = vi.fn();
vi.mock("@/lib/auth/context", () => ({ useAuth: () => ({ login, verifyMfa }) }));
```

and in the top-level `beforeEach`, after `login.mockReset()…`:

```ts
  login.mockReset().mockResolvedValue(null);
  verifyMfa.mockReset().mockResolvedValue({ recoveryCodesRemaining: null });
```

(Replace the existing `login.mockReset().mockResolvedValue(undefined);` line —
`null` is now the "signed in" result.) Then append:

```tsx
describe("LoginForm second factor", () => {
  it("swaps to a code field when the backend asks for one", async () => {
    const user = userEvent.setup();
    login.mockResolvedValue({ mfaToken: "mfa-1" });
    render(<LoginForm />);

    await user.type(screen.getByLabelText("Email"), "me@example.com");
    await user.type(screen.getByLabelText("Password"), "hunter2222");
    await user.click(screen.getByRole("button", { name: /continue/i }));

    expect(await screen.findByLabelText("Authentication code")).toHaveFocus();
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it("sends the code with the challenge token and then signs in", async () => {
    const user = userEvent.setup();
    login.mockResolvedValue({ mfaToken: "mfa-1" });
    render(<LoginForm />);
    await user.type(screen.getByLabelText("Email"), "me@example.com");
    await user.type(screen.getByLabelText("Password"), "hunter2222");
    await user.click(screen.getByRole("button", { name: /continue/i }));

    await user.type(await screen.findByLabelText("Authentication code"), "123456");
    await user.click(screen.getByRole("button", { name: /verify/i }));

    await waitFor(() => expect(verifyMfa).toHaveBeenCalledWith("mfa-1", "123456"));
    await waitFor(() => expect(replace).toHaveBeenCalled());
  });

  it("offers a recovery-code mode that changes the placeholder", async () => {
    const user = userEvent.setup();
    login.mockResolvedValue({ mfaToken: "mfa-1" });
    render(<LoginForm />);
    await user.type(screen.getByLabelText("Email"), "me@example.com");
    await user.type(screen.getByLabelText("Password"), "hunter2222");
    await user.click(screen.getByRole("button", { name: /continue/i }));
    await screen.findByLabelText("Authentication code");

    await user.click(screen.getByRole("button", { name: /use a recovery code/i }));

    expect(screen.getByLabelText("Recovery code")).toBeInTheDocument();
  });

  it("shows the refusal and stays on the code step", async () => {
    const user = userEvent.setup();
    login.mockResolvedValue({ mfaToken: "mfa-1" });
    verifyMfa.mockRejectedValue(
      new ApiError(401, "Unauthorized", { detail: "That code is not valid." }),
    );
    render(<LoginForm />);
    await user.type(screen.getByLabelText("Email"), "me@example.com");
    await user.type(screen.getByLabelText("Password"), "hunter2222");
    await user.click(screen.getByRole("button", { name: /continue/i }));

    await user.type(await screen.findByLabelText("Authentication code"), "000000");
    await user.click(screen.getByRole("button", { name: /verify/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/not valid/i);
    expect(screen.getByLabelText("Authentication code")).toBeInTheDocument();
  });

  it("goes back to the password step on cancel", async () => {
    const user = userEvent.setup();
    login.mockResolvedValue({ mfaToken: "mfa-1" });
    render(<LoginForm />);
    await user.type(screen.getByLabelText("Email"), "me@example.com");
    await user.type(screen.getByLabelText("Password"), "hunter2222");
    await user.click(screen.getByRole("button", { name: /continue/i }));
    await screen.findByLabelText("Authentication code");

    await user.click(screen.getByRole("button", { name: /back/i }));

    expect(screen.getByLabelText("Password")).toBeInTheDocument();
  });
});
```

Add `import { ApiError } from "@/lib/api/errors";` to that file's imports.

- [ ] **Step 3: Run them to verify they fail**

```bash
cd frontend && npx vitest run src/components/login-form.test.tsx
```
Expected: the five new tests FAIL — no code field appears.

- [ ] **Step 4: The API and the context**

In `frontend/src/lib/auth/api.ts`, replace `login` and add:

```ts
export type MfaRequired = Schemas["MfaRequired"];
export type MfaVerifyResponse = Schemas["MfaVerifyResponse"];
/** A signed-in pair, or a challenge to present with a code. */
export type LoginResult = TokenPair | MfaRequired;

/** Exchange credentials for a token pair — or a second-factor challenge. */
export function login(email: string, password: string): Promise<LoginResult> {
  return apiFetch<LoginResult>("/api/v1/auth/login", {
    method: "POST",
    body: { email, password },
    token: null,
  });
}

/** Exchange a challenge token plus a code for the real pair. */
export function verifyMfa(mfaToken: string, code: string): Promise<MfaVerifyResponse> {
  return apiFetch<MfaVerifyResponse>("/api/v1/auth/mfa/verify", {
    method: "POST",
    body: { mfa_token: mfaToken, code },
    token: null,
  });
}

export function isMfaRequired(result: LoginResult): result is MfaRequired {
  return "mfa_required" in result && result.mfa_required === true;
}
```

In `frontend/src/lib/auth/context.tsx`:

```ts
import {
  fetchCurrentUser,
  isMfaRequired,
  login as loginRequest,
  refresh as refreshRequest,
  verifyMfa as verifyMfaRequest,
  type CurrentUser,
} from "@/lib/auth/api";
import type { TokenPair } from "@/lib/auth/session";
```

Replace the `login` member of `AuthContextValue`, and add one:

```ts
  /**
   * Authenticate with credentials. Resolves to `null` once signed in, or to
   * a challenge when a second factor is required; throws `ApiError` on
   * failure.
   */
  login: (email: string, password: string) => Promise<{ mfaToken: string } | null>;
  /** Present a code for a challenge from `login`; signs in on success. */
  verifyMfa: (
    mfaToken: string,
    code: string,
  ) => Promise<{ recoveryCodesRemaining: number | null }>;
```

Replace the `login` callback:

```ts
  // The one place a session is established. Both the password-only path and
  // the second-factor path end here, so "remembered account" and "signed in"
  // cannot drift apart.
  const finishLogin = useCallback(async (tokens: TokenPair) => {
    persistSession(tokens);
    const me = await fetchCurrentUser();
    setUser(me);
    setStatus("authenticated");
    rememberAccount(me);
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      const result = await loginRequest(email, password);
      if (isMfaRequired(result)) {
        // Nothing is persisted and nothing is remembered: an abandoned
        // challenge must leave no trace of the account.
        return { mfaToken: result.mfa_token };
      }
      await finishLogin(result);
      return null;
    },
    [finishLogin],
  );

  const verifyMfa = useCallback(
    async (mfaToken: string, code: string) => {
      const result = await verifyMfaRequest(mfaToken, code);
      await finishLogin(result);
      return { recoveryCodesRemaining: result.recovery_codes_remaining ?? null };
    },
    [finishLogin],
  );
```

and extend the memo:

```ts
  const value = useMemo<AuthContextValue>(
    () => ({ user, status, login, verifyMfa, logout, refreshUser }),
    [user, status, login, verifyMfa, logout, refreshUser],
  );
```

- [ ] **Step 5: The form**

In `frontend/src/components/login-form.tsx`:

```tsx
  const { login, verifyMfa } = useAuth();
```

State beside `submitting`:

```tsx
  // Set when the backend wants a second factor. While set, the form shows a
  // code field instead of the credentials.
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
  const codeRef = useRef<HTMLInputElement>(null);
```

Replace the body of `onSubmit`'s `try`:

```tsx
    try {
      const challenge = await login(email, password);
      if (challenge) {
        setMfaToken(challenge.mfaToken);
        return;
      }
      router.replace(safeRedirect(searchParams.get(REDIRECT_PARAM)));
    } catch (err) {
```

Add two handlers after `onSubmit`:

```tsx
  async function onVerify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!mfaToken) return;
    setError(null);
    setSubmitting(true);
    try {
      const { recoveryCodesRemaining } = await verifyMfa(mfaToken, code);
      if (recoveryCodesRemaining !== null && recoveryCodesRemaining <= 2) {
        toast.warning(
          `${recoveryCodesRemaining} recovery code${recoveryCodesRemaining === 1 ? "" : "s"} left. Generate new ones from your profile.`,
        );
      }
      router.replace(safeRedirect(searchParams.get(REDIRECT_PARAM)));
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 429
          ? "Too many attempts. Please wait a while and try again."
          : err instanceof ApiError
            ? err.detail
            : "Something went wrong. Please try again.",
      );
      setSubmitting(false);
    }
  }

  function cancelMfa() {
    setMfaToken(null);
    setCode("");
    setUseRecovery(false);
    setError(null);
    setPassword("");
    setTimeout(() => passwordRef.current?.focus(), 0);
  }
```

Add `import { toast } from "sonner";` to the imports. Then replace the
`<form …>` block with a conditional:

```tsx
          {mfaToken ? (
            <form className="space-y-3" onSubmit={onVerify} noValidate>
              <p className="text-muted-foreground text-sm">
                {useRecovery
                  ? "Enter one of your recovery codes."
                  : "Enter the code from your authenticator app."}
              </p>
              <Input
                ref={codeRef}
                name="code"
                autoFocus
                inputMode={useRecovery ? "text" : "numeric"}
                autoComplete="one-time-code"
                placeholder={useRecovery ? "xxxxx-xxxxx" : "123456"}
                aria-label={useRecovery ? "Recovery code" : "Authentication code"}
                required
                value={code}
                onChange={(e) => setCode(e.target.value)}
                disabled={submitting}
              />
              {error ? (
                <p role="alert" className="text-destructive text-sm">
                  {error}
                </p>
              ) : null}
              <Button type="submit" className="w-full" disabled={submitting || !code}>
                {submitting ? "Verifying…" : "Verify"}
              </Button>
              <div className="flex justify-between text-sm">
                <Button type="button" variant="link" size="sm" className="h-auto p-0" onClick={cancelMfa}>
                  Back
                </Button>
                <Button
                  type="button"
                  variant="link"
                  size="sm"
                  className="h-auto p-0"
                  onClick={() => {
                    setUseRecovery((v) => !v);
                    setCode("");
                    codeRef.current?.focus();
                  }}
                >
                  {useRecovery ? "Use your authenticator app" : "Use a recovery code instead"}
                </Button>
              </div>
            </form>
          ) : (
            <form className="space-y-3" onSubmit={onSubmit} noValidate>
              … the existing form, unchanged …
            </form>
          )}
```

(Keep the existing form's contents exactly as they are inside the `else`
branch, including the forgot-password link.)

- [ ] **Step 6: Run the tests, typecheck, lint, commit**

```bash
cd frontend && npx vitest run src/components/login-form.test.tsx src/lib/auth && npm run typecheck && npm run lint
git add frontend/src/lib/auth frontend/src/lib/api/generated/schema.ts frontend/src/components/login-form.tsx frontend/src/components/login-form.test.tsx
git commit -m "feat(login): the second-factor step

The auth context returns a challenge instead of signing in when the backend
asks for one; the form swaps its credential fields for a code field. Nothing is
persisted and no account is remembered until the real tokens arrive, so an
abandoned challenge leaves no trace.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: The profile card

Four states: off, setting up, just enabled, on. The QR is drawn in the browser
from the URI the backend returns.

**Files:**
- Modify: `frontend/package.json` (via `npm install`)
- Modify: `frontend/src/lib/api/resources/users.ts`
- Create: `frontend/src/components/profile/totp-card.tsx`
- Modify: `frontend/src/components/profile/profile-view.tsx`
- Test: `frontend/src/components/profile/totp-card.test.tsx`

**Interfaces:**
- Consumes: `users.totpSetup()`, `users.totpEnable(code)`, `users.totpDisable(code)`, `users.totpRegenerate(code)`, `refreshUser` from the auth context.
- Produces: `TotpCard({ enabled, onChanged })`.

- [ ] **Step 1: Install the QR renderer and add the calls**

```bash
cd frontend && npm install qrcode.react@4.2.0
```

In `frontend/src/lib/api/resources/users.ts`:

```ts
export type TotpSetup = Schemas["TotpSetup"];
export type TotpCodes = Schemas["TotpCodes"];
```

```ts
  /** Start enrolling an authenticator app. 2FA stays off until confirmed. */
  totpSetup: () => api.post<TotpSetup>(`${BASE}/me/totp/setup`, {}),
  /** Prove the app works; returns the recovery codes exactly once. */
  totpEnable: (code: string) => api.post<TotpCodes>(`${BASE}/me/totp/enable`, { code }),
  /** Turn 2FA off. A valid code is required. */
  totpDisable: (code: string) => api.post<void>(`${BASE}/me/totp/disable`, { code }),
  /** Replace every recovery code. A valid code is required. */
  totpRegenerate: (code: string) => api.post<TotpCodes>(`${BASE}/me/totp/recovery-codes`, { code }),
  /** Admin: turn off another user's 2FA. No code — the lost-phone backstop. */
  adminTotpDisable: (id: number) => api.post<void>(`${BASE}/${id}/totp/disable`, {}),
```

Re-export `TotpSetup` and `TotpCodes` from `frontend/src/lib/api/index.ts`.

- [ ] **Step 2: Write the failing tests**

Create `frontend/src/components/profile/totp-card.test.tsx`:

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

vi.mock("qrcode.react", () => ({
  QRCodeSVG: ({ value }: { value: string }) => <div data-testid="qr">{value}</div>,
}));

import { users } from "@/lib/api";
import { ApiError } from "@/lib/api/errors";
import { TotpCard } from "@/components/profile/totp-card";

const SETUP = { secret: "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", otpauth_uri: "otpauth://totp/MegooPM:me?secret=GEZD" };
const CODES = { codes: Array.from({ length: 10 }, (_, i) => `ABCDE-FGHJ${i}`) };

beforeEach(() => {
  vi.spyOn(toast, "success").mockImplementation(() => "" as never);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("TotpCard when off", () => {
  it("offers to enable", () => {
    render(<TotpCard enabled={false} onChanged={() => {}} />);
    expect(screen.getByRole("button", { name: /enable/i })).toBeInTheDocument();
  });

  it("shows the QR and the secret after setup starts", async () => {
    const user = userEvent.setup();
    vi.spyOn(users, "totpSetup").mockResolvedValue(SETUP);
    render(<TotpCard enabled={false} onChanged={() => {}} />);

    await user.click(screen.getByRole("button", { name: /enable/i }));

    expect(await screen.findByTestId("qr")).toHaveTextContent(SETUP.otpauth_uri);
    expect(screen.getByText(/GEZD GNBV/)).toBeInTheDocument();
  });

  it("shows the recovery codes once after a correct code", async () => {
    const user = userEvent.setup();
    vi.spyOn(users, "totpSetup").mockResolvedValue(SETUP);
    vi.spyOn(users, "totpEnable").mockResolvedValue(CODES);
    const onChanged = vi.fn();
    render(<TotpCard enabled={false} onChanged={onChanged} />);
    await user.click(screen.getByRole("button", { name: /enable/i }));
    await screen.findByTestId("qr");

    await user.type(screen.getByLabelText("Code from your app"), "123456");
    await user.click(screen.getByRole("button", { name: /confirm/i }));

    expect(await screen.findByText("ABCDE-FGHJ0")).toBeInTheDocument();
    expect(screen.getByText(/only time/i)).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalled();
  });

  it("keeps the setup screen up on a wrong code", async () => {
    const user = userEvent.setup();
    vi.spyOn(users, "totpSetup").mockResolvedValue(SETUP);
    vi.spyOn(users, "totpEnable").mockRejectedValue(
      new ApiError(400, "Bad request", { detail: "That code is not valid." }),
    );
    render(<TotpCard enabled={false} onChanged={() => {}} />);
    await user.click(screen.getByRole("button", { name: /enable/i }));
    await screen.findByTestId("qr");

    await user.type(screen.getByLabelText("Code from your app"), "000000");
    await user.click(screen.getByRole("button", { name: /confirm/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/not valid/i);
    expect(screen.getByTestId("qr")).toBeInTheDocument();
  });

  it("requires acknowledgement before leaving the codes screen", async () => {
    const user = userEvent.setup();
    vi.spyOn(users, "totpSetup").mockResolvedValue(SETUP);
    vi.spyOn(users, "totpEnable").mockResolvedValue(CODES);
    render(<TotpCard enabled={false} onChanged={() => {}} />);
    await user.click(screen.getByRole("button", { name: /enable/i }));
    await user.type(await screen.findByLabelText("Code from your app"), "123456");
    await user.click(screen.getByRole("button", { name: /confirm/i }));
    await screen.findByText("ABCDE-FGHJ0");

    expect(screen.getByRole("button", { name: /done/i })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: /saved these/i }));
    expect(screen.getByRole("button", { name: /done/i })).toBeEnabled();
  });
});

describe("TotpCard when on", () => {
  it("offers regenerate and disable, each asking for a code", async () => {
    const user = userEvent.setup();
    render(<TotpCard enabled onChanged={() => {}} />);

    await user.click(screen.getByRole("button", { name: /disable/i }));

    expect(screen.getByLabelText("Code")).toBeInTheDocument();
  });

  it("disables with a valid code", async () => {
    const user = userEvent.setup();
    const disable = vi.spyOn(users, "totpDisable").mockResolvedValue(undefined);
    const onChanged = vi.fn();
    render(<TotpCard enabled onChanged={onChanged} />);
    await user.click(screen.getByRole("button", { name: /disable/i }));

    await user.type(screen.getByLabelText("Code"), "ABCDE-FGHJ0");
    await user.click(screen.getByRole("button", { name: /turn off/i }));

    await waitFor(() => expect(disable).toHaveBeenCalledWith("ABCDE-FGHJ0"));
    expect(onChanged).toHaveBeenCalled();
  });

  it("regenerates and shows the new codes once", async () => {
    const user = userEvent.setup();
    vi.spyOn(users, "totpRegenerate").mockResolvedValue(CODES);
    render(<TotpCard enabled onChanged={() => {}} />);
    await user.click(screen.getByRole("button", { name: /regenerate/i }));

    await user.type(screen.getByLabelText("Code"), "123456");
    await user.click(screen.getByRole("button", { name: /generate new codes/i }));

    expect(await screen.findByText("ABCDE-FGHJ0")).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run them to verify they fail**

```bash
cd frontend && npx vitest run src/components/profile/totp-card.test.tsx
```
Expected: FAIL — module not found.

- [ ] **Step 4: The card**

Create `frontend/src/components/profile/totp-card.tsx`:

```tsx
"use client";

import { useState } from "react";
import { Copy, ShieldCheck, ShieldOff } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { toast } from "sonner";

import { users, type TotpSetup } from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type Mode =
  | { kind: "idle" }
  | { kind: "setup"; setup: TotpSetup }
  | { kind: "codes"; codes: string[]; reason: "enabled" | "regenerated" }
  | { kind: "ask"; action: "disable" | "regenerate" };

/** Group a base32 secret in fours for reading off a screen into a phone. */
function grouped(secret: string): string {
  return secret.match(/.{1,4}/g)?.join(" ") ?? secret;
}

/**
 * Two-factor authentication, in four states: off, setting up, showing the
 * one-time recovery codes, and on.
 *
 * `enabled` comes from the session user; `onChanged` asks the parent to
 * refresh it, so the card never guesses the server's state.
 */
export function TotpCard({ enabled, onChanged }: { enabled: boolean; onChanged: () => void }) {
  const [mode, setMode] = useState<Mode>({ kind: "idle" });
  const [code, setCode] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function reset() {
    setMode({ kind: "idle" });
    setCode("");
    setAcknowledged(false);
    setError(null);
  }

  async function run(fn: () => Promise<void>) {
    setError(null);
    setBusy(true);
    try {
      await fn();
    } catch (err) {
      setError(describeError(err).message);
    } finally {
      setBusy(false);
    }
  }

  const start = () =>
    run(async () => {
      setMode({ kind: "setup", setup: await users.totpSetup() });
    });

  const confirm = () =>
    run(async () => {
      const { codes } = await users.totpEnable(code);
      setCode("");
      setMode({ kind: "codes", codes, reason: "enabled" });
      onChanged();
    });

  const disable = () =>
    run(async () => {
      await users.totpDisable(code);
      toast.success("Two-factor authentication turned off");
      reset();
      onChanged();
    });

  const regenerate = () =>
    run(async () => {
      const { codes } = await users.totpRegenerate(code);
      setCode("");
      setMode({ kind: "codes", codes, reason: "regenerated" });
    });

  async function copyCodes(codes: string[]) {
    try {
      await navigator.clipboard.writeText(codes.join("\n"));
      toast.success("Recovery codes copied");
    } catch {
      toast.error("Could not copy. Select the codes and copy them by hand.");
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          {enabled ? <ShieldCheck className="size-4 text-success" /> : <ShieldOff className="size-4 text-muted-foreground" />}
          Two-factor authentication
        </CardTitle>
        <CardDescription>
          {enabled
            ? "Signing in needs your password and a code from your authenticator app."
            : "Add a second step to signing in, using an authenticator app on your phone."}
        </CardDescription>
      </CardHeader>

      <CardContent className="grid gap-4">
        {mode.kind === "setup" ? (
          <>
            <div className="flex flex-col items-center gap-3 sm:flex-row sm:items-start">
              <div className="rounded-lg border bg-white p-3">
                <QRCodeSVG value={mode.setup.otpauth_uri} size={160} />
              </div>
              <div className="space-y-2 text-sm">
                <p>Scan this with your authenticator app, or enter the key by hand:</p>
                <code className="block rounded bg-muted px-2 py-1 font-mono text-xs tracking-wider">
                  {grouped(mode.setup.secret)}
                </code>
                <p className="text-muted-foreground text-xs">
                  Then enter the six-digit code the app shows to confirm it works. Nothing is
                  turned on until you do.
                </p>
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="totp-confirm">Code from your app</Label>
              <Input
                id="totp-confirm"
                inputMode="numeric"
                autoComplete="one-time-code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                disabled={busy}
              />
            </div>
          </>
        ) : null}

        {mode.kind === "codes" ? (
          <div className="space-y-3">
            <p className="text-sm font-medium">
              {mode.reason === "enabled"
                ? "Two-factor authentication is on. Save these recovery codes."
                : "Your old recovery codes no longer work. Save these."}
            </p>
            <p className="text-muted-foreground text-sm">
              Each code signs you in once if you lose your phone. This is the{" "}
              <strong>only time</strong> they will be shown.
            </p>
            <ul className="grid grid-cols-2 gap-1 rounded-lg border bg-muted p-3 font-mono text-sm">
              {mode.codes.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
            <Button variant="outline" size="sm" onClick={() => void copyCodes(mode.codes)}>
              <Copy /> Copy all
            </Button>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(e) => setAcknowledged(e.target.checked)}
                aria-label="I have saved these codes"
              />
              I have saved these codes somewhere safe.
            </label>
          </div>
        ) : null}

        {mode.kind === "ask" ? (
          <div className="space-y-1.5">
            <Label htmlFor="totp-ask">Code</Label>
            <Input
              id="totp-ask"
              autoComplete="one-time-code"
              placeholder="From your app, or a recovery code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              disabled={busy}
            />
            <p className="text-muted-foreground text-xs">
              {mode.action === "disable"
                ? "A code is required to turn this off, so a stolen session cannot."
                : "A code is required. Your current recovery codes will stop working."}
            </p>
          </div>
        ) : null}

        {error ? (
          <p role="alert" className="text-destructive text-sm">
            {error}
          </p>
        ) : null}
      </CardContent>

      <CardFooter className="justify-end gap-2">
        {mode.kind === "idle" && !enabled ? (
          <Button onClick={() => void start()} disabled={busy}>
            Enable
          </Button>
        ) : null}
        {mode.kind === "idle" && enabled ? (
          <>
            <Button variant="outline" onClick={() => setMode({ kind: "ask", action: "regenerate" })}>
              Regenerate recovery codes
            </Button>
            <Button variant="destructive" onClick={() => setMode({ kind: "ask", action: "disable" })}>
              Disable
            </Button>
          </>
        ) : null}
        {mode.kind === "setup" ? (
          <>
            <Button variant="outline" onClick={reset} disabled={busy}>
              Cancel
            </Button>
            <Button onClick={() => void confirm()} disabled={busy || code.length < 6}>
              Confirm
            </Button>
          </>
        ) : null}
        {mode.kind === "codes" ? (
          <Button onClick={reset} disabled={!acknowledged}>
            Done
          </Button>
        ) : null}
        {mode.kind === "ask" ? (
          <>
            <Button variant="outline" onClick={reset} disabled={busy}>
              Cancel
            </Button>
            {mode.action === "disable" ? (
              <Button variant="destructive" onClick={() => void disable()} disabled={busy || !code}>
                Turn off
              </Button>
            ) : (
              <Button onClick={() => void regenerate()} disabled={busy || !code}>
                Generate new codes
              </Button>
            )}
          </>
        ) : null}
      </CardFooter>
    </Card>
  );
}
```

- [ ] **Step 5: Mount it**

In `frontend/src/components/profile/profile-view.tsx`, add
`import { TotpCard } from "@/components/profile/totp-card";` and, after the
Password `</Card>` and before the closing `</div>`:

```tsx
      <TotpCard enabled={user?.totp_enabled ?? false} onChanged={() => void refreshUser()} />
```

- [ ] **Step 6: Run, typecheck, lint, commit**

```bash
cd frontend && npx vitest run src/components/profile && npm run typecheck && npm run lint
git add frontend/package.json frontend/package-lock.json frontend/src/lib/api \
        frontend/src/components/profile
git commit -m "feat(profile): the two-factor card

Four states: off, setting up, the one-time recovery-code reveal, and on. The
QR is drawn in the browser from the URI the backend returns. Done is disabled
until the user says they saved the codes — this is the only time they exist in
plaintext anywhere.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: The users page

A 2FA column and the admin backstop.

**Files:**
- Modify: `frontend/src/components/users/users-view.tsx`
- Test: `frontend/src/components/users/users-view.test.tsx` (append)

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/components/users/users-view.test.tsx`:

```tsx
describe("UsersView two-factor", () => {
  const withTotp = { ...member, id: 4, email: "totp@example.com", full_name: "Totp User", totp_enabled: true };

  beforeEach(() => {
    vi.mocked(fetchCapabilities).mockResolvedValue({ password_reset: false });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows On for a user with 2FA and Off otherwise", async () => {
    vi.spyOn(users, "list").mockResolvedValue([admin, withTotp]);
    render(<UsersView />);
    const on = (await screen.findByText("totp@example.com")).closest("tr")!;
    const off = screen.getByText("admin@example.com").closest("tr")!;
    expect(within(on).getByText("On")).toBeInTheDocument();
    expect(within(off).getByText("Off")).toBeInTheDocument();
  });

  it("offers Disable 2FA only where it is on", async () => {
    vi.spyOn(users, "list").mockResolvedValue([admin, withTotp]);
    render(<UsersView />);
    const on = (await screen.findByText("totp@example.com")).closest("tr")!;
    const off = screen.getByText("admin@example.com").closest("tr")!;
    expect(within(on).getByRole("button", { name: /disable 2fa for totp@example.com/i })).toBeInTheDocument();
    expect(within(off).queryByRole("button", { name: /disable 2fa/i })).not.toBeInTheDocument();
  });

  it("confirms, then calls the admin route", async () => {
    const user = userEvent.setup();
    vi.spyOn(users, "list").mockResolvedValue([admin, withTotp]);
    const disable = vi.spyOn(users, "adminTotpDisable").mockResolvedValue(undefined);
    render(<UsersView />);
    const on = (await screen.findByText("totp@example.com")).closest("tr")!;

    await user.click(within(on).getByRole("button", { name: /disable 2fa for/i }));
    expect(await screen.findByText(/Disable two-factor/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "confirm-delete" }));

    await waitFor(() => expect(disable).toHaveBeenCalledWith(4));
  });
});
```

The existing `ConfirmDeleteDialog` stub renders its `title` and a
`confirm-delete` button, which is what the last test drives.

- [ ] **Step 2: Run them to verify they fail**

```bash
cd frontend && npx vitest run src/components/users/users-view.test.tsx
```
Expected: the three new tests FAIL.

- [ ] **Step 3: The column and the action**

In `frontend/src/components/users/users-view.tsx`:

Add `ShieldOff` to the lucide import. Add state beside `deleteTarget`:

```tsx
  const [totpTarget, setTotpTarget] = useState<User | null>(null);
```

Add the header after `Status`:

```tsx
                <TableHead>2FA</TableHead>
```

and bump the empty-row `colSpan={6}` to `colSpan={7}`. Add the cell after the
Status cell:

```tsx
                      <TableCell>
                        <Badge variant={u.totp_enabled ? "success" : "muted"}>
                          {u.totp_enabled ? "On" : "Off"}
                        </Badge>
                      </TableCell>
```

In the actions cell, after the resend button:

```tsx
                          {u.totp_enabled ? (
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              aria-label={`Disable 2FA for ${u.email}`}
                              onClick={() => setTotpTarget(u)}
                            >
                              <ShieldOff />
                            </Button>
                          ) : null}
```

After the existing `ConfirmDeleteDialog`, a second one for this action —
the component is a generic confirm with a destructive button, which is
exactly the shape this needs:

```tsx
      <ConfirmDeleteDialog
        open={totpTarget !== null}
        onOpenChange={(open) => {
          if (!open) setTotpTarget(null);
        }}
        title="Disable two-factor authentication?"
        description={
          totpTarget
            ? `Turn off two-factor authentication for ${displayName(totpTarget)} (${totpTarget.email})? They will be signed out everywhere and emailed that you did this. Use this only when they have lost their authenticator and their recovery codes.`
            : ""
        }
        onConfirm={async () => {
          if (totpTarget) await users.adminTotpDisable(totpTarget.id);
        }}
        onDeleted={refresh}
      />
```

- [ ] **Step 4: Run everything, commit, tear down**

```bash
cd frontend && npx vitest run src/components/users && npm run typecheck && npm run lint && npm test
git add frontend/src/components/users
git commit -m "feat(users): the 2FA column and the admin backstop

Disable 2FA appears only where it is on, and the confirmation says the user
will be signed out everywhere and emailed — this is for the case where they
have lost both the authenticator and the recovery codes, and it should read
that way.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
docker rm -f megoopm-test megoopm-testdb && docker network rm megoopm-testnet
```

---

## Manual verification

With the stack up and a real authenticator app:

- [ ] Enable from the profile. Scan the QR with the app; the confirm code
      is accepted; ten recovery codes appear once. Reload the page: they are
      gone.
- [ ] Sign out and in. The code step appears; a current code signs you in;
      the same code a second time within a minute is refused.
- [ ] Sign in with a recovery code. It works once; the warning about
      remaining codes appears when two or fewer are left.
- [ ] Enter ten wrong codes. The eleventh attempt is refused for rate, not
      for the code.
- [ ] From a second browser, sign in and stay signed in. Disable 2FA from
      the first. The second browser's next refresh lands on the login page.
- [ ] As an admin, disable 2FA on another user. They receive the email
      naming you; their next sign-in has no code step.
- [ ] Check the server's clock against the phone's. A drift over thirty
      seconds makes every code wrong and looks like a bug.
