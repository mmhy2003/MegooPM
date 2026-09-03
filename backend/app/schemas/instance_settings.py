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
from typing import Any
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from app.models.enums import CrowdSecBanMode, DefaultSiteMode, HubUpdateFrequency, SmtpSecurity

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


def validate_app_url(value: str) -> str:
    """Accept only a plain absolute http(s) URL.

    Deliberately not :func:`validate_redirect_url`: that one also bans quotes,
    backslash, ``;`` and ``$`` because its output is written into an nginx
    directive. This value never reaches a config file, and over-restricting it
    would reject legitimate URLs for a rule that does not apply here.
    """
    stripped = value.strip()
    if not stripped:
        raise ValueError("app URL must not be empty")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in stripped):
        raise ValueError("app URL must not contain control characters")
    parsed = urlsplit(stripped)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ValueError("app URL must start with http:// or https://")
    if not parsed.netloc:
        raise ValueError("app URL must include a host")
    return stripped.rstrip("/")


def reject_newlines(value: str, field: str) -> str:
    """Refuse a value that could inject an email header.

    A CR or LF ends the current header and begins another, letting an attacker
    append a ``Bcc:`` of their choosing to every message the system sends.
    """
    if "\r" in value or "\n" in value:
        raise ValueError(f"{field} must not contain a newline")
    return value


class InstanceSettingsRead(BaseModel):
    """Public representation of the settings singleton.

    The LLM API key and the SMTP password are deliberately absent.
    ``llm_api_key_set`` and ``smtp_password_set`` say whether one is stored; the
    values themselves are never returned by any endpoint, so a compromised
    browser session cannot read them back out.
    """

    model_config = ConfigDict(from_attributes=True)

    default_site_mode: DefaultSiteMode
    default_site_redirect_url: str | None
    default_site_page_id: int | None
    crowdsec_ban_mode: CrowdSecBanMode
    crowdsec_ban_page_id: int | None
    llm_enabled: bool
    llm_model: str | None
    llm_api_base: str | None
    llm_api_key_set: bool
    smtp_enabled: bool
    smtp_host: str | None
    smtp_port: int
    smtp_security: SmtpSecurity
    smtp_username: str | None
    smtp_password_set: bool
    smtp_from: str | None
    smtp_from_name: str | None
    app_url: str | None
    crowdsec_hub_auto_update: bool
    crowdsec_hub_update_frequency: HubUpdateFrequency
    crowdsec_hub_update_weekday: int
    crowdsec_hub_update_hour_utc: int
    crowdsec_capi_enabled: bool
    updated_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> InstanceSettingsRead:
        """Build from an ORM row, deriving ``llm_api_key_set`` without the key.

        A classmethod rather than ``model_validate`` so there is exactly one way
        to build this, and no path where a caller hands over the raw row and the
        ciphertext leaks into a response.
        """
        return cls(
            default_site_mode=row.default_site_mode,
            default_site_redirect_url=row.default_site_redirect_url,
            default_site_page_id=row.default_site_page_id,
            crowdsec_ban_mode=row.crowdsec_ban_mode,
            crowdsec_ban_page_id=row.crowdsec_ban_page_id,
            llm_enabled=row.llm_enabled,
            llm_model=row.llm_model,
            llm_api_base=row.llm_api_base,
            llm_api_key_set=row.llm_api_key_enc is not None,
            smtp_enabled=row.smtp_enabled,
            smtp_host=row.smtp_host,
            smtp_port=row.smtp_port,
            smtp_security=row.smtp_security,
            smtp_username=row.smtp_username,
            smtp_password_set=row.smtp_password_enc is not None,
            smtp_from=row.smtp_from,
            smtp_from_name=row.smtp_from_name,
            app_url=row.app_url,
            crowdsec_hub_auto_update=row.crowdsec_hub_auto_update,
            crowdsec_hub_update_frequency=row.crowdsec_hub_update_frequency,
            crowdsec_hub_update_weekday=row.crowdsec_hub_update_weekday,
            crowdsec_hub_update_hour_utc=row.crowdsec_hub_update_hour_utc,
            crowdsec_capi_enabled=row.crowdsec_capi_enabled,
            updated_at=row.updated_at,
        )


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


class CrowdSecBanUpdate(BaseModel):
    """Set the CrowdSec ban page. ``crowdsec_ban_mode`` is required.

    Required for the same reason ``default_site_mode`` is on its sibling:
    "custom_page needs a page" cannot be checked against a payload that omits
    the mode, and a schema never sees the stored row.
    """

    crowdsec_ban_mode: CrowdSecBanMode
    crowdsec_ban_page_id: int | None = Field(
        default=None, description="Required when the mode is 'custom_page'"
    )

    @model_validator(mode="after")
    def _coherent(self) -> CrowdSecBanUpdate:
        """Mirror the database CHECK constraint, with a usable message."""
        if (
            self.crowdsec_ban_mode is CrowdSecBanMode.custom_page
            and self.crowdsec_ban_page_id is None
        ):
            raise ValueError("crowdsec_ban_page_id is required when the mode is 'custom_page'")
        return self


class CrowdSecHubUpdate(BaseModel):
    """The hub refresh schedule. ``hour_utc`` is UTC; the UI converts."""

    auto_update: bool
    frequency: HubUpdateFrequency
    weekday: int = Field(ge=0, le=6, description="Monday = 0; used when weekly")
    hour_utc: int = Field(ge=0, le=23)


class CrowdSecCapiUpdate(BaseModel):
    """Desired state of the community blocklist. Applying it restarts CrowdSec."""

    enabled: bool


class LlmSettingsUpdate(BaseModel):
    """Set the LLM integration. Carries the whole group; the key is the exception.

    ``llm_enabled`` is required for the same reason ``default_site_mode`` is on
    its sibling: "enabled needs a model" cannot be checked against a payload
    that omits it, and a schema never sees the stored row.

    ``llm_api_key`` is the one field that cannot work that way — it is never
    returned, so a client has nothing to send back. Absent keeps the stored key;
    a string replaces it; an explicit ``null`` clears it. The three states are
    distinguished with ``model_fields_set``, which is why the service is handed
    ``model_dump(exclude_unset=True)``.
    """

    llm_enabled: bool
    llm_model: str | None = None
    llm_api_base: str | None = None
    llm_api_key: str | None = None

    @field_validator("llm_model", "llm_api_base", "llm_api_key")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        """An empty input box means "not set", not "the empty string"."""
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _enabled_needs_a_model(self) -> LlmSettingsUpdate:
        if self.llm_enabled and not self.llm_model:
            raise ValueError("llm_model is required when llm_enabled is true")
        return self


class LlmTestRequest(BaseModel):
    """Optional overrides for the probe, so a key can be checked before saving."""

    model: str | None = None
    api_base: str | None = None
    api_key: str | None = None


class LlmTestResult(BaseModel):
    """The probe's outcome. ``ok: false`` still returns HTTP 200 — see the route."""

    ok: bool
    model: str
    reply: str = ""
    error: str = ""
    latency_ms: int = 0


class SmtpSettingsUpdate(BaseModel):
    """Set the SMTP group. Carries the whole card; the password is the exception.

    ``smtp_enabled`` is required for the same reason ``default_site_mode`` is on
    its sibling: "enabled needs a host" cannot be checked against a payload that
    omits it, and a schema never sees the stored row.

    ``smtp_password`` is never returned, so a client has nothing to send back.
    Absent keeps the stored password; a string replaces it; an explicit ``null``
    clears it — distinguished with ``model_fields_set``, which is why the service
    is handed ``model_dump(exclude_unset=True)``.
    """

    smtp_enabled: bool
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: SmtpSecurity = SmtpSecurity.starttls
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_from_name: str | None = None
    app_url: str | None = None

    @field_validator(
        "smtp_host", "smtp_username", "smtp_password", "smtp_from", "smtp_from_name", "app_url"
    )
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        """An empty input box means "not set", not "the empty string"."""
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("smtp_from")
    @classmethod
    def _clean_from(cls, value: str | None) -> str | None:
        return None if value is None else reject_newlines(value, "smtp_from")

    @field_validator("smtp_from_name")
    @classmethod
    def _clean_from_name(cls, value: str | None) -> str | None:
        return None if value is None else reject_newlines(value, "smtp_from_name")

    @field_validator("app_url")
    @classmethod
    def _clean_app_url(cls, value: str | None) -> str | None:
        return None if value is None else validate_app_url(value)

    @model_validator(mode="after")
    def _coherent(self) -> SmtpSettingsUpdate:
        """Mirror the database CHECK constraint, with a usable message."""
        if self.smtp_enabled and not self.smtp_host:
            raise ValueError("smtp_host is required when smtp_enabled is true")
        # Not a database constraint, but every receiving server rejects a
        # message with no From, so an enabled card without one cannot work.
        if self.smtp_enabled and not self.smtp_from:
            raise ValueError("smtp_from is required when smtp_enabled is true")
        return self


class MailTestRequest(BaseModel):
    """Where to send the test. Omitted means the requesting admin's own address."""

    to: EmailStr | None = None


class MailTestResult(BaseModel):
    """The send's outcome. ``ok: false`` still returns HTTP 200 — see the route."""

    ok: bool
    detail: str = ""
    latency_ms: int = 0


__all__ = [
    "CrowdSecCapiUpdate",
    "CrowdSecHubUpdate",
    "InstanceSettingsRead",
    "MailTestRequest",
    "MailTestResult",
    "SmtpSettingsUpdate",
    "reject_newlines",
    "validate_app_url",
    "InstanceSettingsUpdate",
    "LlmSettingsUpdate",
    "LlmTestRequest",
    "LlmTestResult",
    "validate_redirect_url",
]
