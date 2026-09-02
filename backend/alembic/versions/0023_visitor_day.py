"""Per-IP-per-day visitor aggregates

One row per distinct visitor per day rather than one per request: the table
grows with visitors (thousands) instead of requests (millions), and pruning is
a single DELETE on an indexed column.

Revision ID: 0023_visitor_day
Revises: 0022_node_metrics
Create Date: 2026-09-02 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0023_visitor_day"
down_revision: str | None = "0022_node_metrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "visitor_day",
        sa.Column("ip", postgresql.INET(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("request_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.PrimaryKeyConstraint("ip", "day", name=op.f("pk_visitor_day")),
    )
    # The prune deletes by day and the dashboard reads recent days; both scan on
    # `day` alone, which the composite primary key (ip first) cannot serve.
    op.create_index(op.f("ix_visitor_day_day"), "visitor_day", ["day"])


def downgrade() -> None:
    op.drop_index(op.f("ix_visitor_day_day"), table_name="visitor_day")
    op.drop_table("visitor_day")
