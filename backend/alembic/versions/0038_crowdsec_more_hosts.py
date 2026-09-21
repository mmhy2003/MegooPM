"""Enforce CrowdSec bans on redirection and 404 hosts

Until now only proxy hosts ran the bouncer, so an IP CrowdSec banned for what
it did against a redirect or a parked domain was never blocked there. The
column defaults to true, and the server default fills existing rows: the gap
closes on deploy rather than host by host.

Revision ID: 0038_crowdsec_more_hosts
Revises: 0037_nginx_apply_status
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0038_crowdsec_more_hosts"
down_revision: str | None = "0037_nginx_apply_status"
branch_labels: str | None = None
depends_on: str | None = None

_TABLES = ("redirection_hosts", "dead_hosts")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("crowdsec_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "crowdsec_enabled")
