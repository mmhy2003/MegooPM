"""Proxy host locations: a path answered with a branded error page

A fifth target. The server block already maps every branded status to its
document, so such a location needs only to ``return`` the code — nginx's own
``error_page`` turns it into whatever Settings -> Error pages says for that
code, including a custom page bound there.

Adding a value to a PostgreSQL enum has to commit before anything may use it,
so the ALTER TYPE runs in its own autocommit block; the constraint that names
the new value follows in the ordinary transaction.

Revision ID: 0033_location_error_page
Revises: 0032_error_pages
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0033_location_error_page"
down_revision: str | None = "0032_error_pages"
branch_labels: str | None = None
depends_on: str | None = None

_OLD_CHECK = (
    "(target = 'pool' AND upstream_id IS NOT NULL AND forward_host IS NULL"
    " AND forward_port IS NULL AND custom_page_id IS NULL)"
    " OR (target = 'host' AND upstream_id IS NULL AND forward_host IS NOT NULL"
    " AND forward_port IS NOT NULL AND custom_page_id IS NULL)"
    " OR (target = 'default_site' AND upstream_id IS NULL AND forward_host IS NULL"
    " AND forward_port IS NULL AND custom_page_id IS NULL)"
    " OR (target = 'custom_page' AND upstream_id IS NULL AND forward_host IS NULL"
    " AND forward_port IS NULL AND custom_page_id IS NOT NULL)"
)

_NEW_CHECK = (
    "(target = 'pool' AND upstream_id IS NOT NULL AND forward_host IS NULL"
    " AND forward_port IS NULL AND custom_page_id IS NULL AND error_code IS NULL)"
    " OR (target = 'host' AND upstream_id IS NULL AND forward_host IS NOT NULL"
    " AND forward_port IS NOT NULL AND custom_page_id IS NULL AND error_code IS NULL)"
    " OR (target = 'default_site' AND upstream_id IS NULL AND forward_host IS NULL"
    " AND forward_port IS NULL AND custom_page_id IS NULL AND error_code IS NULL)"
    " OR (target = 'custom_page' AND upstream_id IS NULL AND forward_host IS NULL"
    " AND forward_port IS NULL AND custom_page_id IS NOT NULL AND error_code IS NULL)"
    " OR (target = 'error_page' AND upstream_id IS NULL AND forward_host IS NULL"
    " AND forward_port IS NULL AND custom_page_id IS NULL AND error_code IS NOT NULL)"
)


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # ADD VALUE must be committed before the constraint below can name it.
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE location_target ADD VALUE IF NOT EXISTS 'error_page'")

    op.add_column(
        "proxy_host_locations",
        sa.Column("error_code", sa.SmallInteger(), nullable=True),
    )
    op.drop_constraint("location_target_exactly_one", "proxy_host_locations", type_="check")
    op.create_check_constraint("location_target_exactly_one", "proxy_host_locations", _NEW_CHECK)


def downgrade() -> None:
    # These rows have no shape the old constraint allows.
    op.execute("DELETE FROM proxy_host_locations WHERE target = 'error_page'")
    op.drop_constraint("location_target_exactly_one", "proxy_host_locations", type_="check")
    op.create_check_constraint("location_target_exactly_one", "proxy_host_locations", _OLD_CHECK)
    op.drop_column("proxy_host_locations", "error_code")
    # The enum value stays: PostgreSQL cannot drop one, and an unused label is
    # inert. A re-upgrade finds it already there and moves on.
