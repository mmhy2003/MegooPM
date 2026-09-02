"""Instance-settings domain service.

One row, always ``id=1``, seeded by migration ``0019``. No FastAPI imports —
callers pass an :class:`~sqlalchemy.ext.asyncio.AsyncSession`.

Setting the default site clears the columns the new mode does not use. The
database CHECK constraints only require that the *relevant* column is present,
so a stale redirect URL could otherwise survive a switch to ``not_found``:
invisible in the rendered config, but it would reappear in the form if the
operator switched back, showing a URL they believed they had left behind.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret, encrypt_secret
from app.models.enums import CrowdSecBanMode, DefaultSiteMode
from app.models.instance_settings import InstanceSettings
from app.services.llm import LlmConfig

SETTINGS_ID = 1


class UnknownCustomPageError(Exception):
    """Raised when the default site references a custom page that does not exist."""


async def get_instance_settings(db: AsyncSession) -> InstanceSettings:
    """Return the singleton, creating it if a hand-migrated database lacks it."""
    row = await db.get(InstanceSettings, SETTINGS_ID)
    if row is None:
        row = InstanceSettings(id=SETTINGS_ID, default_site_mode=DefaultSiteMode.not_found)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def update_default_site(db: AsyncSession, changes: dict[str, Any]) -> InstanceSettings:
    """Apply a coherent default-site payload, clearing the unused columns."""
    row = await get_instance_settings(db)
    mode = changes["default_site_mode"]

    row.default_site_mode = mode
    row.default_site_redirect_url = (
        changes.get("default_site_redirect_url") if mode is DefaultSiteMode.redirect else None
    )
    row.default_site_page_id = (
        changes.get("default_site_page_id") if mode is DefaultSiteMode.custom_page else None
    )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        # The only FK here is the custom page, so a violation means the id is bogus.
        raise UnknownCustomPageError(str(exc.orig)) from exc
    await db.refresh(row)
    return row


async def update_ban_page(db: AsyncSession, changes: dict[str, Any]) -> InstanceSettings:
    """Apply a coherent ban-page payload, clearing the unused column."""
    row = await get_instance_settings(db)
    mode = changes["crowdsec_ban_mode"]

    row.crowdsec_ban_mode = mode
    row.crowdsec_ban_page_id = (
        changes.get("crowdsec_ban_page_id") if mode is CrowdSecBanMode.custom_page else None
    )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        # The only FK touched here is the custom page, so a violation means the
        # id is bogus.
        raise UnknownCustomPageError(str(exc.orig)) from exc
    await db.refresh(row)
    return row


async def update_llm_settings(db: AsyncSession, changes: dict[str, Any]) -> InstanceSettings:
    """Apply an LLM settings payload, encrypting the key on the way in.

    ``changes`` must come from ``model_dump(exclude_unset=True)``: the presence
    or absence of ``llm_api_key`` is the signal for keep-vs-replace-vs-clear,
    and a plain dump would flatten "absent" into ``None`` and silently wipe a
    working key on every save.
    """
    row = await get_instance_settings(db)

    row.llm_enabled = changes["llm_enabled"]
    row.llm_model = changes.get("llm_model")
    row.llm_api_base = changes.get("llm_api_base")

    if "llm_api_key" in changes:
        key = changes["llm_api_key"]
        row.llm_api_key_enc = encrypt_secret(key) if key else None

    await db.commit()
    await db.refresh(row)
    return row


def llm_config_from_row(row: InstanceSettings) -> LlmConfig:
    """Decrypt the stored key into a config the LLM service can use."""
    return LlmConfig(
        model=row.llm_model or "",
        api_key=decrypt_secret(row.llm_api_key_enc) if row.llm_api_key_enc else None,
        api_base=row.llm_api_base,
    )


__all__ = [
    "SETTINGS_ID",
    "UnknownCustomPageError",
    "get_instance_settings",
    "llm_config_from_row",
    "update_default_site",
    "update_llm_settings",
]
