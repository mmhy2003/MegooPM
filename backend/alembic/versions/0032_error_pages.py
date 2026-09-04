"""Branded HTTP error pages: what each common status is answered with

One row per configured code; an absent row means the shipped page. Purely
additive — nothing existing changes, and a downgrade only drops what this
created.

Revision ID: 0032_error_pages
Revises: 0031_location_targets
Create Date: 2026-09-05 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0032_error_pages"
down_revision: str | None = "0031_location_targets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MODE = sa.Enum("default", "custom_page", name="error_page_mode")


def upgrade() -> None:
    op.create_table(
        "error_page",
        sa.Column("code", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("mode", _MODE, nullable=False),
        sa.Column("custom_page_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(mode = 'custom_page' AND custom_page_id IS NOT NULL)"
            " OR (mode = 'default' AND custom_page_id IS NULL)",
            name="error_page_mode_needs_page",
        ),
        sa.ForeignKeyConstraint(
            ["custom_page_id"],
            ["custom_pages.id"],
            name=op.f("fk_error_page_custom_page_id_custom_pages"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_error_page")),
    )
    op.create_index(op.f("ix_error_page_custom_page_id"), "error_page", ["custom_page_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_error_page_custom_page_id"), table_name="error_page")
    op.drop_table("error_page")
    # create_table emitted CREATE TYPE for the enum; dropping the table does
    # not drop the type, so it goes by hand.
    _MODE.drop(op.get_bind(), checkfirst=True)
