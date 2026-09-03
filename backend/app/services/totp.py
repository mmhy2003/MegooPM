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


def _looks_like_totp(code: str) -> str | None:
    """The six digits if ``code`` is shaped like a TOTP, else ``None``."""
    digits = code.strip().replace(" ", "")
    return digits if len(digits) == 6 and digits.isdigit() else None


def verify_totp(
    secret: str, code: str, *, last_step: int | None, now: datetime | None = None
) -> int | None:
    """Return the time-step ``code`` matched, or ``None``.

    Accepts the current step and one either side. Refuses any step not later
    than ``last_step`` — that is the replay guard.
    """
    digits = _looks_like_totp(code)
    if digits is None:
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


async def _unused_codes(db: AsyncSession, user: User) -> list[RecoveryCode]:
    result = await db.execute(
        select(RecoveryCode).where(RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None))
    )
    return list(result.scalars().all())


async def recovery_codes_remaining(db: AsyncSession, user: User) -> int:
    return len(await _unused_codes(db, user))


# --- verifying ------------------------------------------------------------------


def is_totp_shaped(code: str) -> bool:
    """Six digits is a TOTP; anything else is treated as a recovery code."""
    return _looks_like_totp(code) is not None


async def verify_code(db: AsyncSession, user: User, code: str) -> bool:
    """Accept a TOTP or a recovery code, by shape. Commits on success.

    A pending secret never verifies: 2FA is off until it is proven.
    """
    if not user.totp_enabled or not user.totp_secret_enc:
        return False

    if is_totp_shaped(code):
        step = verify_totp(
            decrypt_secret(user.totp_secret_enc), code, last_step=user.totp_last_step
        )
        if step is None:
            return False
        user.totp_last_step = step
        await db.commit()
        return True

    wanted = _normalise_recovery(code)
    # Deliberately slow — Argon2 per candidate — and only on the recovery
    # path, which is rare by construction.
    for row in await _unused_codes(db, user):
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
    # Passkeys ride on top of TOTP; turning it off takes them with it, so a
    # user whose 2FA was cleared starts clean.
    from app.services import passkeys  # local: avoids a services-level cycle

    await passkeys.delete_all(db, user.id)
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
    "is_totp_shaped",
    "mint_recovery_codes",
    "provisioning_uri",
    "recovery_codes_remaining",
    "start_enrolment",
    "verify_code",
    "verify_totp",
]
