"""CrowdSec ban page selection on the instance-settings singleton

Two columns: which page a blocked visitor is served, and the custom page it
refers to. Defaults to the MegooPM page, so an upgrade replaces the previous
bare 403 without anyone visiting Settings.

Revision ID: 0021_crowdsec_ban_page
Revises: 0020_llm_settings
Create Date: 2026-09-02 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021_crowdsec_ban_page"
down_revision: str | None = "0020_llm_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BAN_MODE = sa.Enum(
    "megoopm",
    "custom_page",
    "none",
    name="crowdsec_ban_mode",
)


def upgrade() -> None:
    # UNLIKE 0019, the type is created by hand here. That migration relies on
    # create_table emitting the CREATE TYPE itself and warns against a second
    # attempt; op.add_column emits no such thing, so without this line the
    # ALTER fails with UndefinedObject. The two comments look contradictory and
    # are not: the difference is create_table vs add_column.
    _BAN_MODE.create(op.get_bind(), checkfirst=False)
    op.add_column(
        "instance_settings",
        sa.Column(
            "crowdsec_ban_mode",
            _BAN_MODE,
            nullable=False,
            server_default="megoopm",
        ),
    )
    op.add_column(
        "instance_settings",
        sa.Column("crowdsec_ban_page_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_instance_settings_crowdsec_ban_page_id_custom_pages"),
        "instance_settings",
        "custom_pages",
        ["crowdsec_ban_page_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_instance_settings_crowdsec_ban_page_id"),
        "instance_settings",
        ["crowdsec_ban_page_id"],
    )
    # Bare name: the ck_%(table_name)s_%(constraint_name)s convention is applied
    # by alembic, so an expanded name would be double-prefixed.
    op.create_check_constraint(
        "ban_custom_page_needs_page",
        "instance_settings",
        "crowdsec_ban_mode <> 'custom_page' OR crowdsec_ban_page_id IS NOT NULL",
    )


def downgrade() -> None:
    # The constraint goes first: dropping a column it references would fail.
    op.drop_constraint(
        op.f("ck_instance_settings_ban_custom_page_needs_page"),
        "instance_settings",
        type_="check",
    )
    op.drop_index(op.f("ix_instance_settings_crowdsec_ban_page_id"), "instance_settings")
    op.drop_constraint(
        op.f("fk_instance_settings_crowdsec_ban_page_id_custom_pages"),
        "instance_settings",
        type_="foreignkey",
    )
    op.drop_column("instance_settings", "crowdsec_ban_page_id")
    op.drop_column("instance_settings", "crowdsec_ban_mode")
    # drop_column leaves the type behind; without this the next upgrade fails
    # with DuplicateObjectError.
    _BAN_MODE.drop(op.get_bind(), checkfirst=True)
