"""Proxy hosts may forward to a single host:port instead of a pool

``upstream_id`` becomes nullable and ``forward_host``/``forward_port`` are added,
with a check constraint enforcing exactly one target. Existing rows all carry a
pool with the new columns NULL, so they satisfy it unchanged — there is no data
migration on the way up.

**DOWNGRADE DELETES PROXY HOSTS.** Restoring ``NOT NULL`` requires removing every
host-targeted row, and on this table that is the vhost itself rather than a
detail of one: the sites those hosts serve stop being served. Take a backup
first. The delete is explicit below rather than left to surface as a constraint
violation.

Revision ID: 0014_host_forward_target
Revises: 0013_stream_upstream
Create Date: 2026-08-31 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0014_host_forward_target"
down_revision: str | None = "0013_stream_upstream"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Bare names: the metadata naming convention (ck_%(table_name)s_%(constraint_name)s)
# is applied by alembic, so passing the expanded name would double the prefix.
_TARGET_CK = "host_target_exactly_one"
_PORT_CK = "forward_port_range"

_TARGET_SQL = (
    "(forward_host IS NOT NULL AND forward_port IS NOT NULL AND upstream_id IS NULL)"
    " OR (forward_host IS NULL AND forward_port IS NULL AND upstream_id IS NOT NULL)"
)


def upgrade() -> None:
    op.alter_column("proxy_hosts", "upstream_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("proxy_hosts", sa.Column("forward_host", sa.String(255), nullable=True))
    op.add_column("proxy_hosts", sa.Column("forward_port", sa.Integer(), nullable=True))
    op.create_check_constraint(
        _PORT_CK, "proxy_hosts", "forward_port IS NULL OR forward_port BETWEEN 1 AND 65535"
    )
    op.create_check_constraint(_TARGET_CK, "proxy_hosts", _TARGET_SQL)


def downgrade() -> None:
    op.drop_constraint(_TARGET_CK, "proxy_hosts", type_="check")
    op.drop_constraint(_PORT_CK, "proxy_hosts", type_="check")
    # Lossy, and this is the vhost: a host-targeted row has no pool to fall back
    # to, so restoring NOT NULL means deleting it and the site it serves.
    op.execute("DELETE FROM proxy_hosts WHERE upstream_id IS NULL")
    op.drop_column("proxy_hosts", "forward_port")
    op.drop_column("proxy_hosts", "forward_host")
    op.alter_column("proxy_hosts", "upstream_id", existing_type=sa.Integer(), nullable=False)
