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
    for junk in ("", "abc", "12345", "1234567", "28a082", "28-7082"):
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


async def test_start_again_replaces_the_pending_secret(session_factory, admin_user: User) -> None:
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


async def test_verify_accepts_each_recovery_code_once(session_factory, admin_user: User) -> None:
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


async def test_disable_deletes_passkeys_too(session_factory, admin_user: User) -> None:
    from app.models.passkey import Passkey
    from app.services import passkeys

    db, user, _, _ = await _enabled(session_factory, admin_user)
    async with db:
        await passkeys.add(db, user, passkeys.Registered(b"a", b"k", 0, []), name="a")
        await totp.disable(db, user)
        rows = (await db.execute(select(Passkey))).scalars().all()
    assert rows == []
