"""Unit tests for DB-backed CrowdSec credentials + auto-registration (MEG-43).

Covers, all hermetically (SQLite, no running CrowdSec):

* the Fernet secret helper (round-trip, wrong-key rejection);
* the credentials accessor (encrypted at rest, resolve, env→DB seed, cache);
* auto-registration (self-registers once, idempotent, no double-register).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.core.config import Settings
from app.core.crypto import SecretDecryptError, decrypt_secret, encrypt_secret
from app.models.crowdsec import CROWDSEC_CREDENTIALS_ROW_ID, CrowdSecCredential
from app.services.crowdsec import credentials as creds_service
from app.services.crowdsec import registration
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    creds_service.invalidate_cache()
    yield
    creds_service.invalidate_cache()


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(
            CrowdSecCredential.metadata.create_all,
            tables=[CrowdSecCredential.__table__],
        )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def _settings(**over: object) -> Settings:
    base: dict[str, object] = {
        "secret_key": "test-secret-key",
        "crowdsec_lapi_url": "http://crowdsec.test:8080",
        "crowdsec_lapi_key": None,
        "crowdsec_machine_id": None,
        "crowdsec_machine_password": None,
    }
    base.update(over)
    return Settings(**base)


# --- crypto ----------------------------------------------------------------


def test_secret_round_trips() -> None:
    s = _settings()
    token = encrypt_secret("s3cr3t-password", settings=s)
    assert token != "s3cr3t-password"
    assert decrypt_secret(token, settings=s) == "s3cr3t-password"


def test_secret_decrypt_wrong_key_raises() -> None:
    token = encrypt_secret("value", settings=_settings(secret_key="key-a"))
    with pytest.raises(SecretDecryptError):
        decrypt_secret(token, settings=_settings(secret_key="key-b"))


# --- credentials accessor --------------------------------------------------


async def test_save_encrypts_secrets_at_rest(session_factory: async_sessionmaker) -> None:
    s = _settings()
    async with session_factory() as db:
        await creds_service.save_credentials(
            db,
            lapi_url="http://lapi:8080",
            machine_id="m1",
            machine_password="pw-plaintext",
            bouncer_key="bk-plaintext",
            settings=s,
        )
        await db.commit()

    async with session_factory() as db:
        row = await db.get(CrowdSecCredential, CROWDSEC_CREDENTIALS_ROW_ID)
        # Never stored in plaintext.
        assert row.machine_password_enc not in (None, "pw-plaintext")
        assert row.bouncer_key_enc not in (None, "bk-plaintext")
        # But decrypts back to the originals.
        assert decrypt_secret(row.machine_password_enc, settings=s) == "pw-plaintext"
        assert decrypt_secret(row.bouncer_key_enc, settings=s) == "bk-plaintext"


async def test_resolve_returns_decrypted_db_creds(session_factory: async_sessionmaker) -> None:
    s = _settings()
    async with session_factory() as db:
        await creds_service.save_credentials(
            db,
            lapi_url="http://lapi:8080",
            machine_id="m1",
            machine_password="pw",
            bouncer_key="bk",
            settings=s,
        )
        await db.commit()

    creds_service.invalidate_cache()
    async with session_factory() as db:
        resolved = await creds_service.resolve(db, settings=s)
    assert resolved is not None
    assert resolved.machine_id == "m1"
    assert resolved.machine_password == "pw"
    assert resolved.bouncer_key == "bk"


async def test_resolve_seeds_from_env_once(session_factory: async_sessionmaker) -> None:
    s = _settings(
        crowdsec_lapi_key="env-bouncer",
        crowdsec_machine_id="env-machine",
        crowdsec_machine_password="env-pass",
    )
    async with session_factory() as db:
        resolved = await creds_service.resolve(db, settings=s)
    assert resolved is not None
    assert resolved.bouncer_key == "env-bouncer"

    # The seed was persisted (encrypted) so later resolves are DB-backed.
    creds_service.invalidate_cache()
    async with session_factory() as db:
        row = await db.get(CrowdSecCredential, CROWDSEC_CREDENTIALS_ROW_ID)
        assert row is not None
        assert row.machine_id == "env-machine"
        assert decrypt_secret(row.machine_password_enc, settings=s) == "env-pass"


async def test_resolve_none_when_unconfigured(session_factory: async_sessionmaker) -> None:
    async with session_factory() as db:
        assert await creds_service.resolve(db, settings=_settings()) is None


async def test_resolve_settings_overlay(session_factory: async_sessionmaker) -> None:
    s = _settings(crowdsec_origin="megoopm")
    async with session_factory() as db:
        await creds_service.save_credentials(
            db, lapi_url="http://db-lapi:8080", machine_id="dbm",
            machine_password="dbp", bouncer_key="dbk", settings=s,
        )
        await db.commit()
    creds_service.invalidate_cache()
    async with session_factory() as db:
        overlaid = await creds_service.resolve_settings(db, settings=s)
    assert overlaid.crowdsec_lapi_url == "http://db-lapi:8080"
    assert overlaid.crowdsec_lapi_key == "dbk"
    assert overlaid.crowdsec_machine_id == "dbm"
    # Non-credential settings are preserved from the base.
    assert overlaid.crowdsec_origin == "megoopm"


# --- auto-registration -----------------------------------------------------


class _FakeClient:
    """Stand-in for CrowdSecClient that records registration calls, no network."""

    registrations: list[tuple[str, str]] = []

    def __init__(self, settings: Settings, *a: object, **k: object) -> None:
        self._settings = settings

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def register_machine(self, machine_id: str, password: str) -> None:
        type(self).registrations.append((machine_id, password))


async def test_ensure_registered_self_registers_once(
    session_factory: async_sessionmaker, monkeypatch
) -> None:
    _FakeClient.registrations = []
    monkeypatch.setattr(registration, "CrowdSecClient", _FakeClient)
    s = _settings()  # no env creds → must self-register

    async with session_factory() as db:
        creds = await registration.ensure_registered(db, settings=s)
    assert creds is not None
    assert creds.machine_id and creds.machine_password
    assert len(_FakeClient.registrations) == 1
    registered_id, registered_pw = _FakeClient.registrations[0]
    assert registered_id == creds.machine_id
    assert registered_pw == creds.machine_password

    # Second call is idempotent — resolves the existing row, no new registration.
    creds_service.invalidate_cache()
    async with session_factory() as db:
        again = await registration.ensure_registered(db, settings=s)
    assert again is not None
    assert again.machine_id == creds.machine_id
    assert len(_FakeClient.registrations) == 1  # not re-registered


async def test_ensure_registered_noop_when_env_present(
    session_factory: async_sessionmaker, monkeypatch
) -> None:
    _FakeClient.registrations = []
    monkeypatch.setattr(registration, "CrowdSecClient", _FakeClient)
    s = _settings(
        crowdsec_machine_id="env-m", crowdsec_machine_password="env-p",
        crowdsec_lapi_key="env-b",
    )
    async with session_factory() as db:
        creds = await registration.ensure_registered(db, settings=s)
    # Env creds seed the DB; no self-registration LAPI call is made.
    assert creds is not None
    assert creds.machine_id == "env-m"
    assert _FakeClient.registrations == []
