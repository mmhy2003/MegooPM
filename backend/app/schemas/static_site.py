"""Pydantic schemas for static sites.

A :class:`~app.models.static_site.StaticSite` serves the files in one folder
under the read-only sites mount (``settings.sites_root``) for a set of domains.
``folder`` is the security boundary: it must be a plain relative path, so no
value can point nginx outside the mount or at a hidden folder. The database
holds the same rule as a check constraint (``ck_static_sites_folder``).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.proxy_host import _normalise_domains

# One or more `/`-joined segments of [A-Za-z0-9._-], none starting with `.`.
# That rules out `..`, hidden folders, absolute paths and empty segments.
FOLDER_PATTERN = r"^[A-Za-z0-9_-][A-Za-z0-9._-]*(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$"
FOLDER_RE = re.compile(FOLDER_PATTERN)
FOLDER_MAX_LENGTH = 255

# What the backend found at a site's folder when it last looked. Advisory only:
# saving never fails on it, the UI shows it as a warning.
FolderStatus = Literal["ok", "no_index", "missing", "unreadable", "symlink", "unmounted"]


def _validate_folder(value: str) -> str:
    if len(value) > FOLDER_MAX_LENGTH:
        raise ValueError(f"folder must be at most {FOLDER_MAX_LENGTH} characters")
    if not FOLDER_RE.match(value):
        raise ValueError(
            "folder must be a relative path of letters, digits, '.', '_' or '-', "
            "with no part starting with '.'"
        )
    return value


class StaticSiteBase(BaseModel):
    """Fields shared by static-site read/write schemas."""

    domain_names: list[str] = Field(
        min_length=1, description="Domains this site answers for (server_name)"
    )
    folder: str = Field(description="Folder under the sites mount holding the site's files")
    certificate_id: int | None = Field(
        default=None, description="Certificate for TLS termination; null serves plain :80"
    )
    access_list_id: int | None = Field(
        default=None, description="Access list guarding the site; null leaves it public"
    )
    ssl_forced: bool = Field(default=False, description="Redirect :80 to HTTPS")
    http2_support: bool = Field(default=False, description="Enable HTTP/2 on the TLS listener")
    hsts_enabled: bool = Field(default=False, description="Emit a Strict-Transport-Security header")
    hsts_subdomains: bool = Field(default=False, description="Include subdomains in HSTS")
    block_exploits: bool = Field(default=False, description="Block common exploit probes")
    crowdsec_enabled: bool = Field(
        default=True, description="Refuse IPs CrowdSec has banned (and inspect with AppSec)"
    )
    advanced_config: str = Field(
        default="", description="Raw nginx directives injected into the server block"
    )
    enabled: bool = Field(default=True, description="Disabled sites are excluded from config")

    @field_validator("domain_names")
    @classmethod
    def _validate_domains(cls, value: list[str]) -> list[str]:
        return _normalise_domains(value)

    @field_validator("folder")
    @classmethod
    def _check_folder(cls, value: str) -> str:
        return _validate_folder(value)


class StaticSiteCreate(StaticSiteBase):
    """Payload to create a static site."""


class StaticSiteUpdate(BaseModel):
    """Partial update of a static site; every field is optional."""

    domain_names: list[str] | None = Field(default=None, min_length=1)
    folder: str | None = None
    certificate_id: int | None = None
    access_list_id: int | None = None
    ssl_forced: bool | None = None
    http2_support: bool | None = None
    hsts_enabled: bool | None = None
    hsts_subdomains: bool | None = None
    block_exploits: bool | None = None
    crowdsec_enabled: bool | None = None
    advanced_config: str | None = None
    enabled: bool | None = None

    @field_validator("domain_names")
    @classmethod
    def _validate_domains(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _normalise_domains(value)

    @field_validator("folder")
    @classmethod
    def _check_folder(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_folder(value)


class StaticSiteRead(StaticSiteBase):
    """Public representation of a static site, with what its folder holds."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
    folder_status: FolderStatus = Field(
        default="ok", description="What the backend found at the folder (advisory)"
    )


__all__ = [
    "FOLDER_MAX_LENGTH",
    "FOLDER_PATTERN",
    "FOLDER_RE",
    "FolderStatus",
    "StaticSiteBase",
    "StaticSiteCreate",
    "StaticSiteRead",
    "StaticSiteUpdate",
]
