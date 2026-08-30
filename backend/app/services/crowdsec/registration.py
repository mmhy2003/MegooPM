"""Automatic CrowdSec machine registration (MEG-43).

On a fresh stack the backend provisions its own LAPI identity instead of relying
on a hand-run ``cscli machines add`` and env vars. :func:`ensure_registered` is
the single entry point; it is **idempotent** and **concurrency-safe**:

1. If credentials already exist (DB row, or an env seed migrated into the DB),
   it returns them and does nothing.
2. Otherwise it self-registers a machine against the configured LAPI
   (``POST /v1/watchers`` with a generated id + strong password) and persists the
   result encrypted.

Concurrency safety comes from two layers so parallel workers never
double-register:

* a Postgres transaction-scoped **advisory lock** (``pg_advisory_xact_lock``)
  serialises the register-then-persist critical section (no-op on SQLite / single
  host); and
* the singleton **primary key** on ``crowdsec_credentials`` — the ultimate
  backstop if two processes on different nodes still race, the PK collision is
  caught and the winner's row is read back.

**Registration mechanism & live-stack caveats** are documented in
``docs/crowdsec.md``. In short: the machine is registered over LAPI HTTP; whether
it is *auto-validated* depends on the LAPI's ``auto_registration`` config (else an
operator/QA runs ``cscli machines validate``). CrowdSec exposes no LAPI HTTP
endpoint to mint a *bouncer* key, so the bouncer key is sourced from env when
present (seeded to the DB) and is otherwise optional — the backend reads
decisions with the bouncer key when it has one and falls back to the machine
token when it does not.
"""

from __future__ import annotations

import contextlib
import logging
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.services.crowdsec import credentials as creds_service
from app.services.crowdsec.client import CrowdSecClient, CrowdSecError
from app.services.crowdsec.credentials import CrowdSecCreds

logger = logging.getLogger(__name__)

# Stable signed-64-bit advisory-lock key ("MEGOcsec" as ASCII). Top bit clear.
_REGISTRATION_LOCK_KEY = 0x4D45474F63736563


@contextlib.asynccontextmanager
async def _registration_lock(db: AsyncSession) -> AsyncIterator[None]:
    """Serialise the register-then-persist section across concurrent workers.

    Transaction-scoped Postgres advisory lock (released on the session's next
    commit/rollback); a no-op on non-Postgres engines, where the singleton PK is
    the sole — and sufficient, single-host — guard.
    """
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:k)"), {"k": _REGISTRATION_LOCK_KEY}
        )
    yield


def _derive_machine_id(settings: Settings) -> str:
    """A stable-ish, unique machine id for this deployment's self-registration."""
    return f"{settings.crowdsec_origin}-{secrets.token_hex(6)}"


def _has_machine(creds: CrowdSecCreds | None) -> bool:
    return bool(creds and creds.machine_id and creds.machine_password)


def _is_usable(creds: CrowdSecCreds | None, settings: Settings) -> bool:
    """True when the stored machine can actually be used against *this* LAPI.

    A machine identity only exists on the LAPI that issued it. If the configured
    endpoint has moved since the row was written, the stored machine is not
    merely stale — it does not exist on the new LAPI — so it must be re-registered
    rather than reused.
    """
    if not _has_machine(creds):
        return False
    return creds.lapi_url == settings.crowdsec_lapi_url


async def _self_register(
    db: AsyncSession, settings: Settings, existing: CrowdSecCreds | None
) -> CrowdSecCreds | None:
    """Register a fresh machine against LAPI and persist it. Returns creds or None.

    ``existing`` is the current row (typically a bouncer-key-only seed from the
    environment); its bouncer key is kept, the machine half is filled in, and the
    endpoint is taken from the current configuration. Best-effort: on any
    LAPI/transport failure it logs and returns
    ``existing`` so a fresh stack whose CrowdSec is not up yet degrades to
    "machine not registered" rather than crashing startup — a later call retries.
    """
    machine_id = _derive_machine_id(settings)
    password = secrets.token_urlsafe(32)
    async with CrowdSecClient(settings) as client:
        try:
            await client.register_machine(
                machine_id, password, registration_token=settings.crowdsec_registration_token
            )
        except CrowdSecError as exc:
            logger.warning("CrowdSec self-registration failed: %s", exc)
            return existing
    await creds_service.save_credentials(
        db,
        # The endpoint we just registered against — never the row's old value,
        # which is what let a moved LAPI keep a machine that does not exist there.
        lapi_url=settings.crowdsec_lapi_url,
        machine_id=machine_id,
        machine_password=password,
        bouncer_key=existing.bouncer_key if existing else None,
        registered_at=datetime.now(UTC),
        settings=settings,
    )
    await db.commit()
    logger.info("CrowdSec machine self-registered as %s", machine_id)
    return await creds_service.resolve(db, settings=settings)


async def ensure_registered(
    db: AsyncSession, *, settings: Settings | None = None
) -> CrowdSecCreds | None:
    """Ensure DB-backed credentials with a *machine* exist, self-registering if needed.

    A bouncer key alone (what every compose stack passes in the environment)
    is not enough: alerts and manual decisions need the machine login, so the
    machine is registered whenever it is missing, keeping the bouncer key.
    Idempotent and concurrency-safe. Returns the resolved credentials, or
    ``None`` if the integration remains unconfigured (e.g. LAPI unreachable on a
    cold start — the caller treats that as "not set up", never an error).
    """
    settings = settings or default_settings
    creds = await creds_service.resolve(db, settings=settings)
    if _is_usable(creds, settings):
        return creds

    async with _registration_lock(db):
        # Re-check under the lock (bypassing the cache): another worker may have
        # registered while we waited for the lock.
        creds_service.invalidate_cache()
        creds = await creds_service.resolve(db, settings=settings)
        if _is_usable(creds, settings):
            return creds
        return await _self_register(db, settings, creds)


__all__ = ["ensure_registered"]
