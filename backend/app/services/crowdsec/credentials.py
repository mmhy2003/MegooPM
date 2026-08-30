"""DB-backed CrowdSec credential accessor (MEG-43).

The single source of truth for the credentials the LAPI client uses. It:

* reads the singleton :class:`CrowdSecCredential` row and decrypts its secrets;
* **seeds** the row from environment variables once, on first use, when the DB
  is empty but ``settings.crowdsec_*`` are set (a zero-touch upgrade path for
  deployments already configured via env);
* caches the resolved credentials in-process and invalidates that cache whenever
  they are written (registration or rotation), so the hot read path never
  re-hits the DB per request.

The client stays credential-source-agnostic: :func:`resolve_settings` overlays
the DB credentials onto the base :class:`Settings`, so the existing
``Settings``-driven :class:`CrowdSecClient` keeps working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.models.crowdsec import CROWDSEC_CREDENTIALS_ROW_ID, CrowdSecCredential


@dataclass(frozen=True)
class CrowdSecCreds:
    """Resolved (decrypted) CrowdSec credentials."""

    lapi_url: str
    machine_id: str | None
    machine_password: str | None
    bouncer_key: str | None
    registered_at: datetime | None


# In-process cache. ``_loaded`` distinguishes "cached: no creds" (a valid,
# cached negative result) from "not yet looked up". Cleared on every write.
_cache: CrowdSecCreds | None = None
_loaded: bool = False


def invalidate_cache() -> None:
    """Drop the in-process credential cache (call after any write/rotation)."""
    global _cache, _loaded
    _cache = None
    _loaded = False


async def load_row(db: AsyncSession) -> CrowdSecCredential | None:
    """Return the singleton credentials row, or ``None`` if unregistered."""
    return await db.get(CrowdSecCredential, CROWDSEC_CREDENTIALS_ROW_ID)


def _decrypt_row(row: CrowdSecCredential, *, settings: Settings) -> CrowdSecCreds:
    return CrowdSecCreds(
        lapi_url=row.lapi_url,
        machine_id=row.machine_id,
        machine_password=(
            decrypt_secret(row.machine_password_enc, settings=settings)
            if row.machine_password_enc
            else None
        ),
        bouncer_key=(
            decrypt_secret(row.bouncer_key_enc, settings=settings)
            if row.bouncer_key_enc
            else None
        ),
        registered_at=row.registered_at,
    )


def _env_creds(settings: Settings) -> CrowdSecCreds | None:
    """Credentials as configured via environment, or ``None`` if none are set."""
    if not (
        settings.crowdsec_lapi_key
        or (settings.crowdsec_machine_id and settings.crowdsec_machine_password)
    ):
        return None
    return CrowdSecCreds(
        lapi_url=settings.crowdsec_lapi_url,
        machine_id=settings.crowdsec_machine_id,
        machine_password=settings.crowdsec_machine_password,
        bouncer_key=settings.crowdsec_lapi_key,
        registered_at=None,
    )


async def save_credentials(
    db: AsyncSession,
    *,
    lapi_url: str,
    machine_id: str | None,
    machine_password: str | None,
    bouncer_key: str | None,
    registered_at: datetime | None = None,
    settings: Settings | None = None,
) -> CrowdSecCredential:
    """Upsert the singleton credentials row, encrypting secrets. Flushes.

    Invalidates the in-process cache. The caller owns the transaction commit.
    """
    settings = settings or default_settings
    row = await load_row(db)
    if row is None:
        row = CrowdSecCredential(id=CROWDSEC_CREDENTIALS_ROW_ID)
        db.add(row)
    row.lapi_url = lapi_url
    row.machine_id = machine_id
    row.machine_password_enc = (
        encrypt_secret(machine_password, settings=settings) if machine_password else None
    )
    row.bouncer_key_enc = (
        encrypt_secret(bouncer_key, settings=settings) if bouncer_key else None
    )
    if registered_at is not None:
        row.registered_at = registered_at
    await db.flush()
    invalidate_cache()
    return row


async def _seed_from_env(db: AsyncSession, env: CrowdSecCreds, settings: Settings) -> CrowdSecCreds:
    """Persist env credentials into the DB once (bootstrap seed).

    Concurrency-safe: if a parallel worker inserts the singleton first, the PK
    collision is caught and the winner's row is read back and returned.
    """
    try:
        async with db.begin_nested():
            await save_credentials(
                db,
                lapi_url=env.lapi_url,
                machine_id=env.machine_id,
                machine_password=env.machine_password,
                bouncer_key=env.bouncer_key,
                registered_at=datetime.now(UTC),
                settings=settings,
            )
        await db.commit()
        return env
    except IntegrityError:
        # Another worker seeded it first — read back the persisted row.
        await db.rollback()
        invalidate_cache()
        row = await load_row(db)
        if row is not None:
            return _decrypt_row(row, settings=settings)
        return env


async def resolve(db: AsyncSession, *, settings: Settings | None = None) -> CrowdSecCreds | None:
    """Resolve the active credentials (DB first, else a one-time env seed).

    Returns ``None`` when neither the DB nor the environment carry credentials
    (the integration is simply unconfigured — the routes report that, not 500).
    Cached in-process; the cache is cleared on any write.
    """
    global _cache, _loaded
    settings = settings or default_settings
    if _loaded:
        return _cache

    row = await load_row(db)
    if row is not None:
        _cache = _decrypt_row(row, settings=settings)
        _loaded = True
        return _cache

    env = _env_creds(settings)
    if env is not None:
        _cache = await _seed_from_env(db, env, settings)
        _loaded = True
        return _cache

    _cache = None
    _loaded = True
    return None


async def resolve_settings(db: AsyncSession, *, settings: Settings | None = None) -> Settings:
    """Return a ``Settings`` with the resolved credentials overlaid.

    The overlay keeps non-credential knobs (origin, timeout) from the base
    settings while swapping in the DB-backed credentials, so the existing
    ``Settings``-driven client needs no changes to read creds from the DB.

    **The endpoint is deliberately not overlaid.** ``CROWDSEC_LAPI_URL`` is
    deployment configuration; only the credentials belong in the database. This
    used to overlay ``creds.lapi_url``, so once the row existed the environment
    was ignored forever: moving from the bundled agent to an external LAPI left
    the backend resolving the old compose service name, and the operator saw a
    DNS error while looking at a correct address in their ``.env``.
    ``creds.lapi_url`` now only records which endpoint the stored identity was
    registered against, so :func:`..registration.ensure_registered` can notice
    the endpoint moved and register a new machine there.
    """
    settings = settings or default_settings
    creds = await resolve(db, settings=settings)
    if creds is None:
        return settings
    return settings.model_copy(
        update={
            "crowdsec_lapi_key": creds.bouncer_key,
            "crowdsec_machine_id": creds.machine_id,
            "crowdsec_machine_password": creds.machine_password,
        }
    )


__all__ = [
    "CrowdSecCreds",
    "invalidate_cache",
    "load_row",
    "resolve",
    "resolve_settings",
    "save_credentials",
]
