"""Passkeys: the relying party, the two ceremonies, and the rows.

py_webauthn does the cryptography. This module decides what the relying party
is, what the options say, what "rejected" means, and what gets stored.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.models.passkey import Passkey
from app.models.user import User
from app.services.mail.templates import APP_NAME

MAX_PASSKEYS = 10
DEFAULT_NAME = "Passkey"


class PasskeysUnavailable(Exception):
    """No app URL, so no relying party."""


class PasskeyRejected(Exception):
    """A ceremony response that does not verify — for any reason."""


class PasskeyLimitReached(Exception):
    pass


class PasskeyDuplicate(Exception):
    pass


@dataclass(frozen=True)
class RelyingParty:
    id: str
    origin: str
    name: str


@dataclass(frozen=True)
class Registered:
    credential_id: bytes
    public_key: bytes
    sign_count: int
    transports: list[str]


# --- relying party ----------------------------------------------------------------


def relying_party(app_url: str | None) -> RelyingParty:
    """Derive RP ID and origin from the app URL setting.

    The RP ID is the hostname; the origin is scheme + host + port with no
    path. Both must match what the browser sees, exactly, or every ceremony
    fails — which is the browser doing its job.
    """
    if not app_url or not app_url.strip():
        raise PasskeysUnavailable()
    parts = urlsplit(app_url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise PasskeysUnavailable()
    host = parts.hostname.lower()
    origin = f"{parts.scheme}://{host}" + (f":{parts.port}" if parts.port else "")
    return RelyingParty(id=host, origin=origin, name=APP_NAME)


# --- ceremonies -----------------------------------------------------------------------


def _descriptor(row: Passkey) -> PublicKeyCredentialDescriptor:
    # ``row.transports`` is None on a row that has not been flushed (the
    # column default applies at INSERT); unknown transport strings are
    # dropped, never raised on.
    transports: list[AuthenticatorTransport] = []
    for value in row.transports or []:
        try:
            transports.append(AuthenticatorTransport(value))
        except ValueError:
            continue
    return PublicKeyCredentialDescriptor(id=row.credential_id, transports=transports or None)


def registration_options(
    rp: RelyingParty, *, user: User, existing: list[Passkey]
) -> tuple[dict, bytes]:
    """Options for ``navigator.credentials.create`` and the challenge to store."""
    options = generate_registration_options(
        rp_id=rp.id,
        rp_name=rp.name,
        user_id=str(user.id).encode(),
        user_name=user.email,
        user_display_name=user.full_name or user.email,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[_descriptor(p) for p in existing],
    )
    return json.loads(options_to_json(options)), options.challenge


def verify_registration(rp: RelyingParty, *, credential: dict, challenge: bytes) -> Registered:
    try:
        result = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp.id,
            expected_origin=rp.origin,
            require_user_verification=False,
        )
    except (WebAuthnException, ValueError, KeyError, TypeError) as exc:
        raise PasskeyRejected() from exc
    response = credential.get("response") if isinstance(credential, dict) else None
    transports = response.get("transports") if isinstance(response, dict) else None
    return Registered(
        credential_id=result.credential_id,
        public_key=result.credential_public_key,
        sign_count=result.sign_count,
        transports=[t for t in (transports or []) if isinstance(t, str)],
    )


def authentication_options(rp: RelyingParty, *, passkeys: list[Passkey]) -> tuple[dict, bytes]:
    """Options for ``navigator.credentials.get`` and the challenge to store."""
    options = generate_authentication_options(
        rp_id=rp.id,
        allow_credentials=[_descriptor(p) for p in passkeys],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    return json.loads(options_to_json(options)), options.challenge


def verify_authentication(
    rp: RelyingParty, *, credential: dict, challenge: bytes, passkey: Passkey
) -> int:
    """Return the authenticator's new sign count, or raise ``PasskeyRejected``."""
    try:
        result = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp.id,
            expected_origin=rp.origin,
            credential_public_key=passkey.public_key,
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=False,
        )
    except (WebAuthnException, ValueError, KeyError, TypeError) as exc:
        raise PasskeyRejected() from exc
    # py_webauthn refuses a count that went backwards when the stored count is
    # non-zero; this restates the rule explicitly so the behaviour is ours
    # and tested, not an accident of the library's version.
    if passkey.sign_count > 0 and result.new_sign_count <= passkey.sign_count:
        raise PasskeyRejected()
    return result.new_sign_count


def credential_id_of(credential: dict) -> bytes | None:
    raw = credential.get("id") if isinstance(credential, dict) else None
    if not isinstance(raw, str) or not raw:
        return None
    try:
        decoded = base64url_to_bytes(raw)
    except Exception:  # noqa: BLE001 — any decode failure means "not a credential id"
        return None
    # The decoder is lenient: junk like "***" comes back as empty bytes.
    return decoded or None


# --- rows --------------------------------------------------------------------------------


async def list_for(db: AsyncSession, user: User) -> list[Passkey]:
    result = await db.execute(
        select(Passkey).where(Passkey.user_id == user.id).order_by(Passkey.created_at, Passkey.id)
    )
    return list(result.scalars().all())


async def add(db: AsyncSession, user: User, registered: Registered, *, name: str) -> Passkey:
    count = await db.scalar(
        select(func.count()).select_from(Passkey).where(Passkey.user_id == user.id)
    )
    if (count or 0) >= MAX_PASSKEYS:
        raise PasskeyLimitReached()
    dup = await db.scalar(
        select(Passkey.id).where(Passkey.credential_id == registered.credential_id)
    )
    if dup is not None:
        raise PasskeyDuplicate()
    row = Passkey(
        user_id=user.id,
        credential_id=registered.credential_id,
        public_key=registered.public_key,
        sign_count=registered.sign_count,
        name=(name or "").strip()[:64] or DEFAULT_NAME,
        transports=registered.transports,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def remove(db: AsyncSession, user: User, passkey_id: int) -> bool:
    result = await db.execute(
        delete(Passkey).where(Passkey.id == passkey_id, Passkey.user_id == user.id)
    )
    await db.commit()
    return bool(result.rowcount)


async def delete_all(db: AsyncSession, user_id: int) -> None:
    """Every passkey for ``user_id``. Does not commit: called inside ``totp.disable``."""
    await db.execute(delete(Passkey).where(Passkey.user_id == user_id))


async def touch(db: AsyncSession, passkey: Passkey, new_sign_count: int) -> None:
    passkey.sign_count = new_sign_count
    passkey.last_used_at = datetime.now(UTC)
    await db.commit()


__all__ = [
    "DEFAULT_NAME",
    "MAX_PASSKEYS",
    "PasskeyDuplicate",
    "PasskeyLimitReached",
    "PasskeyRejected",
    "PasskeysUnavailable",
    "Registered",
    "RelyingParty",
    "add",
    "authentication_options",
    "credential_id_of",
    "delete_all",
    "list_for",
    "registration_options",
    "relying_party",
    "remove",
    "touch",
    "verify_authentication",
    "verify_registration",
]
