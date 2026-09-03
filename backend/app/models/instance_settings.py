"""Instance-wide settings — a single row the whole deployment shares.

One row, always ``id=1``, seeded by the migration so readers never handle "no
row yet" (the same shape as ``crowdsec_whitelist_apply``). Settings are typed
columns rather than a key/value blob: this codebase is typed end to end, and a
JSON value would push validation into hand-written per-key code and cost the
frontend its generated types.

Today it holds one setting — the default site, i.e. what nginx returns for a
request matching no configured host.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Enum, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import (
    CrowdSecBanMode,
    DefaultSiteMode,
    HubUpdateFrequency,
    SmtpSecurity,
)
from app.models.mixins import TimestampMixin


class InstanceSettings(TimestampMixin, Base):
    """The singleton settings row."""

    __tablename__ = "instance_settings"
    __table_args__ = (
        # A half-configured row renders nginx config that says nothing, so the
        # database refuses it as well as the API. Bare names: the metadata
        # naming convention adds the ck_%(table_name)s_ prefix.
        CheckConstraint(
            "default_site_mode <> 'redirect' OR default_site_redirect_url IS NOT NULL",
            name="redirect_needs_url",
        ),
        CheckConstraint(
            "default_site_mode <> 'custom_page' OR default_site_page_id IS NOT NULL",
            name="custom_page_needs_page",
        ),
        CheckConstraint(
            "llm_enabled = false OR llm_model IS NOT NULL",
            name="llm_needs_model",
        ),
        CheckConstraint(
            "smtp_enabled = false OR smtp_host IS NOT NULL",
            name="smtp_needs_host",
        ),
    )

    # Not autoincrement: there is exactly one row and its id is always 1.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False, default=1)

    default_site_mode: Mapped[DefaultSiteMode] = mapped_column(
        Enum(
            DefaultSiteMode,
            name="default_site_mode",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=DefaultSiteMode.not_found,
        server_default=DefaultSiteMode.not_found.value,
    )
    default_site_redirect_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # RESTRICT, not SET NULL: silently changing what every unmatched visitor
    # sees is worse than refusing the delete. (Contrast proxy_hosts.access_list_id,
    # where detaching one host's guard is visible and recoverable.)
    default_site_page_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("custom_pages.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    # --- CrowdSec ban page ----------------------------------------------
    crowdsec_ban_mode: Mapped[CrowdSecBanMode] = mapped_column(
        Enum(
            CrowdSecBanMode,
            name="crowdsec_ban_mode",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=CrowdSecBanMode.megoopm,
        server_default=CrowdSecBanMode.megoopm.value,
    )
    # RESTRICT for the same reason as default_site_page_id: silently changing
    # what every blocked visitor sees is worse than refusing the delete.
    crowdsec_ban_page_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("custom_pages.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    # --- LLM integration -----------------------------------------------
    # Off by default: this opens outbound connections from a reverse proxy's
    # admin backend to a third party, which must never start because an
    # upgrade shipped.
    llm_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # litellm's model string, which already encodes the provider —
    # "gpt-4o", "anthropic/claude-sonnet-4", "ollama/llama3".
    llm_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Only needed when the endpoint is not the provider's default: a local
    # runner, or a gateway.
    llm_api_base: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Fernet token (app.core.crypto), never plaintext. Nullable on purpose:
    # a local model legitimately needs no key.
    llm_api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Outbound email -------------------------------------------------
    # Off by default: an upgrade must never start a reverse proxy's admin
    # backend talking to a mail server nobody configured.
    smtp_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    smtp_host: Mapped[str | None] = mapped_column(Text, nullable=True)
    smtp_port: Mapped[int] = mapped_column(
        Integer, nullable=False, default=587, server_default="587"
    )
    smtp_security: Mapped[SmtpSecurity] = mapped_column(
        Enum(
            SmtpSecurity,
            name="smtp_security",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=SmtpSecurity.starttls,
        server_default=SmtpSecurity.starttls.value,
    )
    smtp_username: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Fernet token (app.core.crypto), never plaintext — as llm_api_key_enc.
    smtp_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    smtp_from: Mapped[str | None] = mapped_column(Text, nullable=True)
    smtp_from_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # This instance's public URL. Unused today — stored here so the operator
    # sets it in the same sitting as the mail server. Password reset and
    # invitations will build links with it; passkeys derive the RP ID from it.
    app_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- CrowdSec maintenance (Security → Updates) ---------------------------
    # The hub refresh schedule. Hour is UTC; the UI converts. Defaults give a
    # fresh install current rules at a quiet hour without visiting the tab.
    crowdsec_hub_auto_update: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    crowdsec_hub_update_frequency: Mapped[HubUpdateFrequency] = mapped_column(
        Enum(
            HubUpdateFrequency,
            name="hub_update_frequency",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=HubUpdateFrequency.daily,
        server_default="daily",
    )
    # Monday = 0. Only consulted when the frequency is weekly.
    crowdsec_hub_update_weekday: Mapped[int] = mapped_column(
        Integer, nullable=False, default=6, server_default="6"
    )
    crowdsec_hub_update_hour_utc: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    # Desired state of the community blocklist. What was *achieved* lives in
    # crowdsec_job_run(kind=capi_apply); the UI shows both when they differ.
    crowdsec_capi_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


__all__ = ["InstanceSettings"]
