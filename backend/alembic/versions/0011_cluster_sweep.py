"""Cluster sweep claims: make periodic sweeps idempotent per period

``leader_lock`` only excludes *concurrent* sweeps. It is held for the body of the
run — for the certificate sweep, the milliseconds it takes to read the due list
and enqueue — so a second node's beat firing moments later takes the free lock
and repeats the work. With ``beat`` on every node (each schedules its own nginx
reconcile) that is routine, and every duplicate drives another ACME order against
Let's Encrypt's five-duplicates-per-week ceiling.

``cluster_sweep`` records the last run per sweep name so the claim can be
conditional on the period having elapsed.

Purely additive and fully reversible.

Revision ID: 0011_cluster_sweep
Revises: 0010_cluster_nodes
Create Date: 2026-08-30 17:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0011_cluster_sweep"
down_revision: str | None = "0010_cluster_nodes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cluster_sweep",
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "last_run_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_cluster_sweep")),
    )


def downgrade() -> None:
    op.drop_table("cluster_sweep")
