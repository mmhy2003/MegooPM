"""Backup schedule, retention, last-run, and the restore audit action

Automatic backups start off: an upgrade must not begin filling a directory the
operator has not chosen yet.

Revision ID: 0039_backups
Revises: 0038_crowdsec_more_hosts
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0039_backups"
down_revision: str | None = "0038_crowdsec_more_hosts"
branch_labels: str | None = None
depends_on: str | None = None

_FREQUENCY = sa.Enum("off", "daily", "weekly", name="backup_frequency")


def upgrade() -> None:
    _FREQUENCY.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "instance_settings",
        sa.Column("backup_frequency", _FREQUENCY, nullable=False, server_default="off"),
    )
    op.add_column(
        "instance_settings",
        sa.Column("backup_hour_utc", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column(
        "instance_settings",
        sa.Column("backup_weekday", sa.Integer(), nullable=False, server_default="6"),
    )
    op.add_column(
        "instance_settings",
        sa.Column("backup_keep", sa.Integer(), nullable=False, server_default="7"),
    )
    op.add_column("instance_settings", sa.Column("last_backup_ok", sa.Boolean(), nullable=True))
    op.add_column(
        "instance_settings",
        sa.Column("last_backup_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("instance_settings", sa.Column("last_backup_message", sa.Text(), nullable=True))
    # Postgres 12+ accepts ADD VALUE inside a transaction; the value is usable
    # from the next statement onwards, which is the next migration or request.
    op.execute("ALTER TYPE audit_action ADD VALUE IF NOT EXISTS 'restore'")


def downgrade() -> None:
    for column in (
        "last_backup_message",
        "last_backup_at",
        "last_backup_ok",
        "backup_keep",
        "backup_weekday",
        "backup_hour_utc",
        "backup_frequency",
    ):
        op.drop_column("instance_settings", column)
    _FREQUENCY.drop(op.get_bind(), checkfirst=True)
    # The enum value stays: Postgres cannot drop one, and an audit row may use it.
