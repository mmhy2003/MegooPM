"""One row per maintenance job kind: the last run and how it went.

The jobs run in Celery on the control-plane node and can fail long after the
API answered 202; this row is what the Updates tab reads. ``finished_at`` is
null while a run is in progress.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import CrowdSecJobKind, CrowdSecJobTrigger


class CrowdSecJobRun(Base):
    __tablename__ = "crowdsec_job_run"

    kind: Mapped[CrowdSecJobKind] = mapped_column(
        Enum(
            CrowdSecJobKind,
            name="crowdsec_job_kind",
            values_callable=lambda e: [m.value for m in e],
        ),
        primary_key=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger: Mapped[CrowdSecJobTrigger] = mapped_column(
        Enum(
            CrowdSecJobTrigger,
            name="crowdsec_job_trigger",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    restarted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # hub_update: {updated: [..], agent_version, latest_agent_version}
    # capi_apply: {enabled: bool}
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")


__all__ = ["CrowdSecJobRun"]
