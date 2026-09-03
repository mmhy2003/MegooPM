"""CrowdSec maintenance: hub schedule + blocklist columns; crowdsec_job_run

Revision ID: 0030_crowdsec_updates
Revises: 0029_passkey
Create Date: 2026-09-04 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0030_crowdsec_updates"
down_revision: str | None = "0029_passkey"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# op.add_column does NOT emit CREATE TYPE for an enum — only create_table does.
_FREQUENCY = sa.Enum("daily", "weekly", name="hub_update_frequency")
_KIND = sa.Enum("hub_update", "capi_apply", name="crowdsec_job_kind")
_TRIGGER = sa.Enum("scheduled", "manual", name="crowdsec_job_trigger")


def upgrade() -> None:
    bind = op.get_bind()
    _FREQUENCY.create(bind, checkfirst=True)
    op.add_column(
        "instance_settings",
        sa.Column("crowdsec_hub_auto_update", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "instance_settings",
        sa.Column(
            "crowdsec_hub_update_frequency", _FREQUENCY, nullable=False, server_default="daily"
        ),
    )
    op.add_column(
        "instance_settings",
        sa.Column("crowdsec_hub_update_weekday", sa.Integer(), nullable=False, server_default="6"),
    )
    op.add_column(
        "instance_settings",
        sa.Column("crowdsec_hub_update_hour_utc", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column(
        "instance_settings",
        sa.Column("crowdsec_capi_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_table(
        "crowdsec_job_run",
        sa.Column("kind", _KIND, nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("trigger", _TRIGGER, nullable=False),
        sa.Column("restarted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("detail", sa.JSON(), nullable=False, server_default="{}"),
        sa.PrimaryKeyConstraint("kind", name=op.f("pk_crowdsec_job_run")),
    )


def downgrade() -> None:
    op.drop_table("crowdsec_job_run")
    _TRIGGER.drop(op.get_bind(), checkfirst=True)
    _KIND.drop(op.get_bind(), checkfirst=True)
    for column in (
        "crowdsec_capi_enabled",
        "crowdsec_hub_update_hour_utc",
        "crowdsec_hub_update_weekday",
        "crowdsec_hub_update_frequency",
        "crowdsec_hub_auto_update",
    ):
        op.drop_column("instance_settings", column)
    _FREQUENCY.drop(op.get_bind(), checkfirst=True)
