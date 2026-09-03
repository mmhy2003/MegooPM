"""Outbound email settings on the instance-settings singleton

Nine columns: the SMTP connection, the Fernet-encrypted password, the From
identity, and this instance's public URL.

Seeded off. Enabling by migration would make the backend start talking to a mail
server nobody configured because an upgrade shipped.

Revision ID: 0024_smtp_settings
Revises: 0023_visitor_day
Create Date: 2026-09-03 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0024_smtp_settings"
down_revision: str | None = "0023_visitor_day"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# op.add_column does NOT emit CREATE TYPE for an enum — only create_table does.
# The type is therefore created and dropped by hand here.
_SECURITY = sa.Enum("starttls", "ssl", "none", name="smtp_security")


def upgrade() -> None:
    bind = op.get_bind()
    _SECURITY.create(bind, checkfirst=True)
    op.add_column(
        "instance_settings",
        sa.Column("smtp_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("instance_settings", sa.Column("smtp_host", sa.Text(), nullable=True))
    op.add_column(
        "instance_settings",
        sa.Column("smtp_port", sa.Integer(), nullable=False, server_default="587"),
    )
    op.add_column(
        "instance_settings",
        sa.Column("smtp_security", _SECURITY, nullable=False, server_default="starttls"),
    )
    op.add_column("instance_settings", sa.Column("smtp_username", sa.Text(), nullable=True))
    op.add_column("instance_settings", sa.Column("smtp_password_enc", sa.Text(), nullable=True))
    op.add_column("instance_settings", sa.Column("smtp_from", sa.Text(), nullable=True))
    op.add_column("instance_settings", sa.Column("smtp_from_name", sa.Text(), nullable=True))
    op.add_column("instance_settings", sa.Column("app_url", sa.Text(), nullable=True))
    # Bare name: the ck_%(table_name)s_%(constraint_name)s convention is applied
    # by alembic, so an expanded name would be double-prefixed.
    op.create_check_constraint(
        "smtp_needs_host",
        "instance_settings",
        "smtp_enabled = false OR smtp_host IS NOT NULL",
    )


def downgrade() -> None:
    # The constraint goes first: dropping a column it references would fail.
    op.drop_constraint(
        op.f("ck_instance_settings_smtp_needs_host"), "instance_settings", type_="check"
    )
    op.drop_column("instance_settings", "app_url")
    op.drop_column("instance_settings", "smtp_from_name")
    op.drop_column("instance_settings", "smtp_from")
    op.drop_column("instance_settings", "smtp_password_enc")
    op.drop_column("instance_settings", "smtp_username")
    op.drop_column("instance_settings", "smtp_security")
    op.drop_column("instance_settings", "smtp_port")
    op.drop_column("instance_settings", "smtp_host")
    op.drop_column("instance_settings", "smtp_enabled")
    _SECURITY.drop(op.get_bind(), checkfirst=True)
