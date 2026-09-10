"""Remember how the last nginx apply went

A failed ``nginx -t`` is rolled back, and the host that caused it stays in the
database, so every later apply fails the same way until someone fixes it. That
is a standing condition the UI has to be able to read — not a line in a worker
log. The columns live on the existing ``cluster_state`` singleton, which
migration 0006 seeds in every deployment, single-host included.

All nullable: NULL means no apply has been recorded since this migration, which
the UI shows as nothing rather than as a failure.

Revision ID: 0037_nginx_apply_status
Revises: 0036_api_keys
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0037_nginx_apply_status"
down_revision: str | None = "0036_api_keys"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("cluster_state", sa.Column("last_apply_ok", sa.Boolean(), nullable=True))
    op.add_column(
        "cluster_state", sa.Column("last_apply_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("cluster_state", sa.Column("last_apply_message", sa.Text(), nullable=True))
    op.add_column("cluster_state", sa.Column("last_apply_output", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("cluster_state", "last_apply_output")
    op.drop_column("cluster_state", "last_apply_message")
    op.drop_column("cluster_state", "last_apply_at")
    op.drop_column("cluster_state", "last_apply_ok")
