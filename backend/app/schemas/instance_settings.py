"""Pydantic schemas for instance settings.

``default_site_redirect_url`` is the one field here that becomes part of a
generated nginx configuration file, so it is validated far more strictly than a
URL field normally would be — see :func:`validate_redirect_url`.

:class:`InstanceSettingsUpdate` requires ``default_site_mode`` even though it is
otherwise a partial update. Coherence ("redirect needs a URL") cannot be checked
against a payload that omits the mode, because whether the rule applies depends
on the stored row, which a schema never sees. Requiring the mode makes the
payload self-describing and matches the UI, where one Save button submits the
whole card.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import DefaultSiteMode

_ALLOWED_SCHEMES = {"http", "https"}

# Characters that would let a redirect target escape its nginx directive:
# quotes close the quoted string, a backslash escapes, ';' ends the directive,
# and '$' interpolates an nginx variable into the target.
_FORBIDDEN = frozenset("\"'\\;$")


def validate_redirect_url(value: str) -> str:
    """Accept only a plain absolute http(s) URL that is inert inside nginx config.

    The order matters: the character scan runs **before** :func:`urlsplit`,
    because Python strips tab, CR and LF from a URL before parsing it (WHATWG
    behaviour). A target containing a newline therefore parses perfectly
    cleanly, and parsing first would let it through into the rendered config
    where the newline ends the ``return`` directive and begins a new one.
    """
    stripped = value.strip()
    if not stripped:
        raise ValueError("redirect URL must not be empty")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in stripped):
        raise ValueError("redirect URL must not contain control characters")
    if any(c in _FORBIDDEN for c in stripped):
        raise ValueError("redirect URL must not contain quotes, a backslash, ';' or '$'")

    parsed = urlsplit(stripped)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ValueError("redirect URL must start with http:// or https://")
    if not parsed.netloc:
        raise ValueError("redirect URL must include a host")
    return stripped


class InstanceSettingsRead(BaseModel):
    """Public representation of the settings singleton."""

    model_config = ConfigDict(from_attributes=True)

    default_site_mode: DefaultSiteMode
    default_site_redirect_url: str | None
    default_site_page_id: int | None
    updated_at: datetime


class InstanceSettingsUpdate(BaseModel):
    """Set the default site. ``default_site_mode`` is required (see module doc)."""

    default_site_mode: DefaultSiteMode
    default_site_redirect_url: str | None = Field(
        default=None, description="Required when the mode is 'redirect'"
    )
    default_site_page_id: int | None = Field(
        default=None, description="Required when the mode is 'custom_page'"
    )

    @field_validator("default_site_redirect_url")
    @classmethod
    def _clean_url(cls, value: str | None) -> str | None:
        return None if value is None else validate_redirect_url(value)

    @model_validator(mode="after")
    def _coherent(self) -> InstanceSettingsUpdate:
        """Mirror the database CHECK constraints, with a usable message."""
        if (
            self.default_site_mode is DefaultSiteMode.redirect
            and not self.default_site_redirect_url
        ):
            raise ValueError("default_site_redirect_url is required when the mode is 'redirect'")
        if (
            self.default_site_mode is DefaultSiteMode.custom_page
            and self.default_site_page_id is None
        ):
            raise ValueError("default_site_page_id is required when the mode is 'custom_page'")
        return self


__all__ = [
    "InstanceSettingsRead",
    "InstanceSettingsUpdate",
    "validate_redirect_url",
]
