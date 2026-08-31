"""Proxy host locations may forward to a single host:port instead of a pool

The same either/or the host itself gained in 0014, applied to each location: a
user who chose a literal backend for ``/`` expects the same choice for ``/api``.

**Downgrade deletes host-targeted locations.** Less severe than 0014's, which
deletes whole vhosts — a location is a detail of a host, so the host survives
and simply stops routing that prefix separately. Still a data loss.

Revision ID: 0015_location_forward_target
Revises: 0014_host_forward_target
Create Date: 2026-08-31 13:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0015_location_forward_target"
down_revision: str | None = "0014_host_forward_target"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Bare names — alembic applies the ck_%(table_name)s_%(constraint_name)s
# convention on top, so the expanded form would double the prefix.
_TARGET_CK = "location_target_exactly_one"
_PORT_CK = "forward_port_range"
_TABLE = "proxy_host_locations"

_TARGET_SQL = (
    "(forward_host IS NOT NULL AND forward_port IS NOT NULL AND upstream_id IS NULL)"
    " OR (forward_host IS NULL AND forward_port IS NULL AND upstream_id IS NOT NULL)"
)


def upgrade() -> None:
    op.alter_column(_TABLE, "upstream_id", existing_type=sa.Integer(), nullable=True)
    op.add_column(_TABLE, sa.Column("forward_host", sa.String(255), nullable=True))
    op.add_column(_TABLE, sa.Column("forward_port", sa.Integer(), nullable=True))
    op.create_check_constraint(
        _PORT_CK, _TABLE, "forward_port IS NULL OR forward_port BETWEEN 1 AND 65535"
    )
    op.create_check_constraint(_TARGET_CK, _TABLE, _TARGET_SQL)


def downgrade() -> None:
    op.drop_constraint(_TARGET_CK, _TABLE, type_="check")
    op.drop_constraint(_PORT_CK, _TABLE, type_="check")
    # Lossy: a host-targeted location has no pool to fall back to.
    op.execute(f"DELETE FROM {_TABLE} WHERE upstream_id IS NULL")
    op.drop_column(_TABLE, "forward_port")
    op.drop_column(_TABLE, "forward_host")
    op.alter_column(_TABLE, "upstream_id", existing_type=sa.Integer(), nullable=False)
