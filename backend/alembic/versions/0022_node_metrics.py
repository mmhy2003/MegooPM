"""Most recent nginx sample per node, for the dashboard's traffic card

One row per node, overwritten on each scrape — not history, so there is no
retention policy to own. No enum columns here, so unlike 0021 there is no type
to create by hand.

Revision ID: 0022_node_metrics
Revises: 0021_crowdsec_ban_page
Create Date: 2026-09-02 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0022_node_metrics"
down_revision: str | None = "0021_crowdsec_ban_page"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "node_metrics",
        sa.Column("node_id", sa.String(length=255), nullable=False),
        sa.Column("active_connections", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requests_total", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("requests_per_second", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "sampled_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("node_id", name=op.f("pk_node_metrics")),
    )


def downgrade() -> None:
    op.drop_table("node_metrics")
