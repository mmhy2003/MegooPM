"""The relying party, the ceremonies with real signatures, and the rows."""

from __future__ import annotations

import pytest
from app.models.passkey import Passkey
from app.models.user import User
from app.services import passkeys

from tests.webauthn_fake import FakeAuthenticator

RP = passkeys.RelyingParty(id="localhost", origin="http://localhost:3000", name="MegooPM")


# --- relying party from the app URL ---------------------------------------


@pytest.mark.parametrize(
    ("app_url", "rp_id", "origin"),
    [
        ("http://localhost:3000", "localhost", "http://localhost:3000"),
        ("https://pm.example.com", "pm.example.com", "https://pm.example.com"),
        ("https://pm.example.com:8443/some/path/", "pm.example.com", "https://pm.example.com:8443"),
        ("https://PM.Example.com/", "pm.example.com", "https://pm.example.com"),
    ],
)
def test_relying_party_derives_from_the_app_url(app_url: str, rp_id: str, origin: str) -> None:
    rp = passkeys.relying_party(app_url)
    assert (rp.id, rp.origin, rp.name) == (rp_id, origin, "MegooPM")


@pytest.mark.parametrize("app_url", [None, "", "   ", "not a url", "localhost:3000"])
def test_relying_party_refuses_a_missing_or_bare_url(app_url: str | None) -> None:
    with pytest.raises(passkeys.PasskeysUnavailable):
        passkeys.relying_party(app_url)


# --- registration, with a real key ----------------------------------------


def _user() -> User:
    return User(id=42, email="me@example.com", full_name="Me", hashed_password="x")


def test_registration_options_name_the_rp_and_the_user_and_exclude_existing() -> None:
    existing = Passkey(user_id=42, credential_id=b"old-id", public_key=b"", sign_count=0)
    options, challenge = passkeys.registration_options(RP, user=_user(), existing=[existing])
    assert options["rp"] == {"id": "localhost", "name": "MegooPM"}
    assert options["user"]["name"] == "me@example.com"
    assert options["attestation"] == "none"
    assert options["authenticatorSelection"]["userVerification"] == "preferred"
    assert options["authenticatorSelection"]["residentKey"] == "preferred"
    assert [c["id"] for c in options["excludeCredentials"]] == ["b2xkLWlk"]
    assert len(challenge) >= 32


def test_a_real_attestation_verifies_and_yields_the_stored_shape() -> None:
    auth = FakeAuthenticator(RP.id, RP.origin)
    options, challenge = passkeys.registration_options(RP, user=_user(), existing=[])

    reg = passkeys.verify_registration(
        RP,
        credential=auth.register(options, transports=("internal", "hybrid")),
        challenge=challenge,
    )

    assert reg.credential_id == auth.credential_id
    assert reg.public_key and reg.sign_count == 0
    assert reg.transports == ["internal", "hybrid"]


def test_registration_from_another_origin_is_rejected() -> None:
    # The whole point of the origin check: a phishing page on another host
    # cannot register a key against this RP.
    auth = FakeAuthenticator(RP.id, "http://evil.example:3000")
    options, challenge = passkeys.registration_options(RP, user=_user(), existing=[])
    with pytest.raises(passkeys.PasskeyRejected):
        passkeys.verify_registration(RP, credential=auth.register(options), challenge=challenge)


def test_registration_against_the_wrong_challenge_is_rejected() -> None:
    auth = FakeAuthenticator(RP.id, RP.origin)
    options, _ = passkeys.registration_options(RP, user=_user(), existing=[])
    with pytest.raises(passkeys.PasskeyRejected):
        passkeys.verify_registration(RP, credential=auth.register(options), challenge=b"other")


def test_garbage_is_rejected_not_raised() -> None:
    with pytest.raises(passkeys.PasskeyRejected):
        passkeys.verify_registration(RP, credential={"id": "x"}, challenge=b"c")


# --- authentication, with a real key --------------------------------------


def _registered() -> tuple[FakeAuthenticator, Passkey]:
    auth = FakeAuthenticator(RP.id, RP.origin)
    options, challenge = passkeys.registration_options(RP, user=_user(), existing=[])
    reg = passkeys.verify_registration(RP, credential=auth.register(options), challenge=challenge)
    row = Passkey(
        user_id=42,
        credential_id=reg.credential_id,
        public_key=reg.public_key,
        sign_count=0,
        transports=reg.transports,
    )
    return auth, row


def test_authentication_options_list_the_users_credentials() -> None:
    _, row = _registered()
    options, challenge = passkeys.authentication_options(RP, passkeys=[row])
    assert options["rpId"] == "localhost"
    assert options["userVerification"] == "preferred"
    assert len(options["allowCredentials"]) == 1
    assert options["allowCredentials"][0]["transports"] == ["internal"]
    assert len(challenge) >= 32


def test_a_real_assertion_verifies_and_returns_the_new_count() -> None:
    auth, row = _registered()
    options, challenge = passkeys.authentication_options(RP, passkeys=[row])
    new_count = passkeys.verify_authentication(
        RP, credential=auth.assert_(options, count=5), challenge=challenge, passkey=row
    )
    assert new_count == 5


def test_a_regressed_count_is_rejected_when_the_stored_count_counts() -> None:
    # A cloned hardware key falls behind the original. Refuse it.
    auth, row = _registered()
    row.sign_count = 9
    options, challenge = passkeys.authentication_options(RP, passkeys=[row])
    with pytest.raises(passkeys.PasskeyRejected):
        passkeys.verify_authentication(
            RP, credential=auth.assert_(options, count=9), challenge=challenge, passkey=row
        )


def test_a_zero_count_is_fine_when_the_stored_count_is_zero() -> None:
    # Synced passkeys (iCloud Keychain, Google Password Manager) report zero
    # forever. A regression check that fired here would lock them all out.
    auth, row = _registered()
    options, challenge = passkeys.authentication_options(RP, passkeys=[row])
    assert (
        passkeys.verify_authentication(
            RP, credential=auth.assert_(options, count=0), challenge=challenge, passkey=row
        )
        == 0
    )


def test_an_assertion_from_a_different_key_is_rejected() -> None:
    _, row = _registered()
    other = FakeAuthenticator(RP.id, RP.origin)
    other.credential_id = row.credential_id  # same id, different key
    options, challenge = passkeys.authentication_options(RP, passkeys=[row])
    with pytest.raises(passkeys.PasskeyRejected):
        passkeys.verify_authentication(
            RP, credential=other.assert_(options, count=1), challenge=challenge, passkey=row
        )


def test_credential_id_of_decodes_or_returns_none() -> None:
    assert passkeys.credential_id_of({"id": "b2xkLWlk"}) == b"old-id"
    assert passkeys.credential_id_of({"id": "***"}) is None
    assert passkeys.credential_id_of({}) is None


# --- rows ----------------------------------------------------------------------


async def test_add_list_remove(session_factory, admin_user: User) -> None:
    async with session_factory() as db:
        user = await db.get(User, admin_user.id)
        reg = passkeys.Registered(b"id-1", b"key", 0, ["internal"])
        row = await passkeys.add(db, user, reg, name="  ")
        assert row.name == "Passkey"  # blank names get the default
        assert [p.id for p in await passkeys.list_for(db, user)] == [row.id]
        assert await passkeys.remove(db, user, row.id) is True
        assert await passkeys.remove(db, user, row.id) is False
        assert await passkeys.list_for(db, user) == []


async def test_add_refuses_duplicates_and_the_cap(session_factory, admin_user: User) -> None:
    async with session_factory() as db:
        user = await db.get(User, admin_user.id)
        await passkeys.add(db, user, passkeys.Registered(b"dup", b"k", 0, []), name="a")
        with pytest.raises(passkeys.PasskeyDuplicate):
            await passkeys.add(db, user, passkeys.Registered(b"dup", b"k", 0, []), name="b")
        for i in range(passkeys.MAX_PASSKEYS - 1):
            await passkeys.add(
                db, user, passkeys.Registered(f"k{i}".encode(), b"k", 0, []), name="x"
            )
        with pytest.raises(passkeys.PasskeyLimitReached):
            await passkeys.add(db, user, passkeys.Registered(b"one-more", b"k", 0, []), name="y")


async def test_remove_is_scoped_to_the_user(
    session_factory, admin_user: User, member_user: User
) -> None:
    async with session_factory() as db:
        admin = await db.get(User, admin_user.id)
        member = await db.get(User, member_user.id)
        row = await passkeys.add(db, admin, passkeys.Registered(b"a", b"k", 0, []), name="a")
        assert await passkeys.remove(db, member, row.id) is False
        assert len(await passkeys.list_for(db, admin)) == 1


async def test_touch_records_the_count_and_the_time(session_factory, admin_user: User) -> None:
    async with session_factory() as db:
        user = await db.get(User, admin_user.id)
        row = await passkeys.add(db, user, passkeys.Registered(b"a", b"k", 0, []), name="a")
        assert row.last_used_at is None
        await passkeys.touch(db, row, 12)
        assert row.sign_count == 12 and row.last_used_at is not None
