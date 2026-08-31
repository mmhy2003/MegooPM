"""Pool context: which nginx context a pool may render into

``upstream`` blocks are context-local — one defined in ``http {}`` is invisible
to ``stream {}`` — so a pool has to declare where it may be attached. This also
constrains its load-balancing method: ``ip_hash`` exists only in ``http``.

Existing rows become ``http``, which is what every pool in the database is
today: they are all referenced by proxy hosts.

Purely additive and fully reversible.

Revision ID: 0012_upstream_context
Revises: 0011_cluster_sweep
Create Date: 2026-08-31 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0012_upstream_context"
down_revision: str | None = "0011_cluster_sweep"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONTEXT = sa.Enum("http", "stream", "both", name="upstream_context")


def upgrade() -> None:
    # Create the type explicitly: add_column would emit it implicitly, but then
    # downgrade could not drop it cleanly.
    _CONTEXT.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "upstreams",
        sa.Column("context", _CONTEXT, server_default="http", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("upstreams", "context")
    _CONTEXT.drop(op.get_bind(), checkfirst=True)
