"""CrowdSec whitelists and the state of their last apply.

A :class:`CrowdSecWhitelist` becomes one YAML document in the parser file the
CrowdSec container reads, dropping matching events before they can become
alerts or decisions. See ``docs/crowdsec.md``.

:class:`CrowdSecWhitelistApply` is a single row (``id=1``) recording whether the
last render reached CrowdSec. The apply runs in a Celery task on the
control-plane node, so it can fail long after the API returned 200; without this
row the UI would show a whitelist as active when CrowdSec has never seen it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, Text, true
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin


class CrowdSecWhitelist(IdMixin, TimestampMixin, Base):
    """One whitelist document: a reason plus the IPs and CIDRs it exempts."""

    __tablename__ = "crowdsec_whitelists"

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    ips: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )
    cidrs: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    __table_args__ = (
        # A whitelist matching nothing is always a mistake, and an empty
        # `whitelist:` block would render without complaint.
        CheckConstraint("cardinality(ips) + cardinality(cidrs) > 0", name="not_empty"),
    )


class CrowdSecWhitelistApply(Base):
    """Singleton (``id=1``) describing the last apply attempt."""

    __tablename__ = "crowdsec_whitelist_apply"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    applied_digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ok: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
