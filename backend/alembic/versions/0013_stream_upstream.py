"""Streams may target an upstream pool instead of a single host:port

A stream forwards to exactly one of the two. ``forward_host``/``forward_port``
become nullable and a nullable ``upstream_id`` is added, with a check
constraint enforcing the either/or so a bad row cannot exist even if a caller
bypasses the API.

Existing rows keep their host and port and satisfy the new constraint as they
stand, so there is no data migration on the way up.

**Downgrade is lossy.** Restoring ``NOT NULL`` on the forward columns requires
deleting every stream that targets a pool, because there is no host:port to put
back. The delete is explicit below rather than left to a constraint violation.

Revision ID: 0013_stream_upstream
Revises: 0012_upstream_context
Create Date: 2026-08-31 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0013_stream_upstream"
down_revision: str | None = "0012_upstream_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Bare names: the metadata naming convention (ck_%(table_name)s_%(constraint_name)s)
# is applied by alembic, so passing the expanded name would double the prefix.
_TARGET_CK = "stream_target_exactly_one"
_PORT_CK = "forward_port_range"


def upgrade() -> None:
    op.alter_column("streams", "forward_host", existing_type=sa.String(255), nullable=True)
    op.alter_column("streams", "forward_port", existing_type=sa.Integer(), nullable=True)
    op.add_column("streams", sa.Column("upstream_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_streams_upstream_id"), "streams", ["upstream_id"])
    op.create_foreign_key(
        op.f("fk_streams_upstream_id_upstreams"),
        "streams",
        "upstreams",
        ["upstream_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # The old range check rejects NULL, which a pool-targeted stream now is.
    op.drop_constraint(_PORT_CK, "streams", type_="check")
    op.create_check_constraint(
        "forward_port_range", "streams", "forward_port IS NULL OR forward_port BETWEEN 1 AND 65535"
    )
    op.create_check_constraint(
        "stream_target_exactly_one",
        "streams",
        "(forward_host IS NOT NULL AND forward_port IS NOT NULL AND upstream_id IS NULL)"
        " OR (forward_host IS NULL AND forward_port IS NULL AND upstream_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(_TARGET_CK, "streams", type_="check")
    op.drop_constraint(_PORT_CK, "streams", type_="check")
    op.create_check_constraint(
        "forward_port_range", "streams", "forward_port BETWEEN 1 AND 65535"
    )
    # Lossy: a pool-targeted stream has no host:port to fall back to.
    op.execute("DELETE FROM streams WHERE upstream_id IS NOT NULL")
    op.drop_constraint(op.f("fk_streams_upstream_id_upstreams"), "streams", type_="foreignkey")
    op.drop_index(op.f("ix_streams_upstream_id"), table_name="streams")
    op.drop_column("streams", "upstream_id")
    op.alter_column("streams", "forward_port", existing_type=sa.Integer(), nullable=False)
    op.alter_column("streams", "forward_host", existing_type=sa.String(255), nullable=False)
