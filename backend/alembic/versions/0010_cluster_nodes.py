"""MEG-35 follow-up: cluster_node registry for per-node reconcile fan-out

Adds ``cluster_node``, one row per node, recording the config version that node
has actually reloaded nginx for plus a ``last_seen_at`` heartbeat.

Why: the original fan-out used a Celery ``Broadcast`` (fanout exchange) queue,
which does not work on the Redis broker — the publish side writes into per-worker
``bcast.*`` lists while the consume side subscribes over pub/sub, so reconcile
messages were delivered, persisted, and never read. The replacement addresses
each node by its own direct queue, which needs a list of live nodes; this table
is that list. It doubles as the convergence view: compare ``applied_version``
here against ``cluster_state.config_version``.

Purely additive and fully reversible.

Revision ID: 0010_cluster_nodes
Revises: 0009_proxy_host_locations
Create Date: 2026-08-30 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0010_cluster_nodes"
down_revision: str | None = "0009_proxy_host_locations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cluster_node",
        sa.Column("node_id", sa.String(length=255), nullable=False),
        sa.Column(
            "applied_version",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("node_id", name=op.f("pk_cluster_node")),
    )
    # Fan-out reads "nodes seen recently", so index the heartbeat.
    op.create_index(
        op.f("ix_cluster_node_last_seen_at"),
        "cluster_node",
        ["last_seen_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_cluster_node_last_seen_at"), table_name="cluster_node")
    op.drop_table("cluster_node")
