"""MEG-35 cluster_state: shared nginx config version for HA

Adds the singleton ``cluster_state`` table that coordinates config propagation
across HA nodes. Row ``id = 1`` holds a monotonically increasing
``config_version`` that every successful nginx apply bumps; each node reloads
its local nginx when it observes a version newer than the one it last applied.

Purely additive and fully reversible.

Revision ID: 0006_cluster_state
Revises: 0005_crowdsec
Create Date: 2026-08-26 20:15:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0006_cluster_state"
down_revision: str | None = "0005_crowdsec"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cluster_state",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column(
            "config_version",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cluster_state")),
    )
    # Seed the singleton row so nodes can bump it without an insert race.
    op.execute("INSERT INTO cluster_state (id, config_version) VALUES (1, 0)")


def downgrade() -> None:
    op.drop_table("cluster_state")
