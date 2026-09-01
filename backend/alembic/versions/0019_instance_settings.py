"""Instance-wide settings singleton, holding the default site

One seeded row (``id=1``) so readers never handle "no row yet". Seeded as
``not_found``, which is exactly what the base nginx config hardcodes today —
seeding ``congratulations`` would match NPM's default but would silently change
what a live instance serves the moment this migration runs.

Revision ID: 0019_instance_settings
Revises: 0018_custom_pages
Create Date: 2026-09-01 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019_instance_settings"
down_revision: str | None = "0018_custom_pages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MODE = sa.Enum(
    "congratulations",
    "not_found",
    "no_response",
    "redirect",
    "custom_page",
    name="default_site_mode",
)


def upgrade() -> None:
    # No explicit _MODE.create(): passing the Enum to create_table emits the
    # CREATE TYPE itself (the convention in 0003), and pre-creating it makes
    # that second attempt fail with DuplicateObjectError. The downgrade still
    # drops it by hand, since drop_table leaves the type behind.
    op.create_table(
        "instance_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("default_site_mode", _MODE, nullable=False, server_default="not_found"),
        sa.Column("default_site_redirect_url", sa.Text(), nullable=True),
        sa.Column("default_site_page_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_foreign_key(
        op.f("fk_instance_settings_default_site_page_id_custom_pages"),
        "instance_settings",
        "custom_pages",
        ["default_site_page_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_instance_settings_default_site_page_id"),
        "instance_settings",
        ["default_site_page_id"],
    )
    # Bare names: the ck_%(table_name)s_%(constraint_name)s convention is
    # applied by alembic, so an expanded name would be double-prefixed.
    op.create_check_constraint(
        "redirect_needs_url",
        "instance_settings",
        "default_site_mode <> 'redirect' OR default_site_redirect_url IS NOT NULL",
    )
    op.create_check_constraint(
        "custom_page_needs_page",
        "instance_settings",
        "default_site_mode <> 'custom_page' OR default_site_page_id IS NOT NULL",
    )
    # Seed the singleton so readers never have to handle "no row yet".
    op.execute("INSERT INTO instance_settings (id, default_site_mode) VALUES (1, 'not_found')")


def downgrade() -> None:
    op.drop_table("instance_settings")
    _MODE.drop(op.get_bind(), checkfirst=True)
