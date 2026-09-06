"""Maintenance mode: a proxy host that answers "we'll be back"

Two shapes at once. On ``proxy_hosts``, the per-host switch and the addresses
that skip it. On ``instance_settings``, the page every host under maintenance
serves, chosen once like the ban page.

The enum is created by ``sa.Enum(...).create`` before the column that uses it:
``op.add_column`` does not emit CREATE TYPE, unlike ``create_table``.

Revision ID: 0035_maintenance_mode
Revises: 0034_capi_credential_health
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035_maintenance_mode"
down_revision: str | None = "0034_capi_credential_health"
branch_labels: str | None = None
depends_on: str | None = None

_MODE = sa.Enum("megoopm", "custom_page", name="maintenance_page_mode")


def upgrade() -> None:
    _MODE.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "proxy_hosts",
        sa.Column("maintenance_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "proxy_hosts",
        sa.Column(
            "maintenance_allow",
            postgresql.ARRAY(sa.String(length=64)),
            nullable=False,
            server_default="{}",
        ),
    )

    op.add_column(
        "instance_settings",
        sa.Column("maintenance_mode", _MODE, nullable=False, server_default="megoopm"),
    )
    op.add_column(
        "instance_settings", sa.Column("maintenance_page_id", sa.BigInteger(), nullable=True)
    )
    op.create_index(
        "ix_instance_settings_maintenance_page_id",
        "instance_settings",
        ["maintenance_page_id"],
    )
    op.create_foreign_key(
        "fk_instance_settings_maintenance_page_id",
        "instance_settings",
        "custom_pages",
        ["maintenance_page_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "instance_settings",
        sa.Column(
            "maintenance_retry_after_minutes", sa.Integer(), nullable=False, server_default="60"
        ),
    )


def downgrade() -> None:
    op.drop_column("instance_settings", "maintenance_retry_after_minutes")
    op.drop_constraint(
        "fk_instance_settings_maintenance_page_id", "instance_settings", type_="foreignkey"
    )
    op.drop_index("ix_instance_settings_maintenance_page_id", table_name="instance_settings")
    op.drop_column("instance_settings", "maintenance_page_id")
    op.drop_column("instance_settings", "maintenance_mode")
    op.drop_column("proxy_hosts", "maintenance_allow")
    op.drop_column("proxy_hosts", "maintenance_enabled")
    # add_column never created it, so nothing else will drop it.
    _MODE.drop(op.get_bind(), checkfirst=True)
