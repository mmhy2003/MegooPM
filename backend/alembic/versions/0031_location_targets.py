"""Proxy host locations: an explicit target, and two that nginx answers itself

A location used to mean "proxy onward", and which of the two ways was inferred
from which columns were null. Two more answers exist now — the instance's
default site, and one named custom page — so the target becomes a column and
the check constraint is written against it: a row claiming ``pool`` while
carrying a ``forward_host`` is now rejected rather than silently rendered.

The backfill reads the shape every existing row already has, so nothing about
a current host changes.

Revision ID: 0031_location_targets
Revises: 0030_crowdsec_updates
Create Date: 2026-09-05 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0031_location_targets"
down_revision: str | None = "0030_crowdsec_updates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# op.add_column does NOT emit CREATE TYPE for an enum — only create_table does.
_TARGET = sa.Enum("pool", "host", "default_site", "custom_page", name="location_target")

_OLD_CHECK = (
    "(forward_host IS NOT NULL AND forward_port IS NOT NULL AND upstream_id IS NULL)"
    " OR (forward_host IS NULL AND forward_port IS NULL AND upstream_id IS NOT NULL)"
)
_NEW_CHECK = (
    "(target = 'pool' AND upstream_id IS NOT NULL AND forward_host IS NULL"
    " AND forward_port IS NULL AND custom_page_id IS NULL)"
    " OR (target = 'host' AND upstream_id IS NULL AND forward_host IS NOT NULL"
    " AND forward_port IS NOT NULL AND custom_page_id IS NULL)"
    " OR (target = 'default_site' AND upstream_id IS NULL AND forward_host IS NULL"
    " AND forward_port IS NULL AND custom_page_id IS NULL)"
    " OR (target = 'custom_page' AND upstream_id IS NULL AND forward_host IS NULL"
    " AND forward_port IS NULL AND custom_page_id IS NOT NULL)"
)


def upgrade() -> None:
    bind = op.get_bind()
    _TARGET.create(bind, checkfirst=True)
    op.add_column(
        "proxy_host_locations",
        # Server default 'pool' only so the column can be added NOT NULL to a
        # populated table; the backfill below sets every row's real value.
        sa.Column("target", _TARGET, nullable=False, server_default="pool"),
    )
    op.add_column(
        "proxy_host_locations", sa.Column("custom_page_id", sa.BigInteger(), nullable=True)
    )
    op.create_index(
        op.f("ix_proxy_host_locations_custom_page_id"),
        "proxy_host_locations",
        ["custom_page_id"],
    )
    op.create_foreign_key(
        op.f("fk_proxy_host_locations_custom_page_id_custom_pages"),
        "proxy_host_locations",
        "custom_pages",
        ["custom_page_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # Backfill from the shape each row already has. The old constraint allowed
    # exactly these two, so every row matches one of them.
    op.execute("UPDATE proxy_host_locations SET target = 'host' WHERE forward_host IS NOT NULL")
    op.execute("UPDATE proxy_host_locations SET target = 'pool' WHERE upstream_id IS NOT NULL")

    op.drop_constraint("location_target_exactly_one", "proxy_host_locations", type_="check")
    op.create_check_constraint("location_target_exactly_one", "proxy_host_locations", _NEW_CHECK)


def downgrade() -> None:
    # Locations nginx answers itself have no equivalent in the old shape; they
    # would violate the restored constraint, so they go.
    op.execute("DELETE FROM proxy_host_locations WHERE target IN ('default_site', 'custom_page')")
    op.drop_constraint("location_target_exactly_one", "proxy_host_locations", type_="check")
    op.create_check_constraint("location_target_exactly_one", "proxy_host_locations", _OLD_CHECK)
    op.drop_constraint(
        op.f("fk_proxy_host_locations_custom_page_id_custom_pages"),
        "proxy_host_locations",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_proxy_host_locations_custom_page_id"), table_name="proxy_host_locations")
    op.drop_column("proxy_host_locations", "custom_page_id")
    op.drop_column("proxy_host_locations", "target")
    _TARGET.drop(op.get_bind(), checkfirst=True)
