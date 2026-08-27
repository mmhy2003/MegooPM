"""Proxy host locations: extra path-prefixed routes to other upstream pools

Adds ``proxy_host_locations``: one row per ``location ^~ <path>`` block a proxy
host forwards to a pool other than its root one. CASCADE from the host,
RESTRICT to the pool (mirrors ``proxy_hosts.upstream_id``). Reuses the
existing ``http_scheme`` enum type.

Purely additive and fully reversible.

Revision ID: 0009_proxy_host_locations
Revises: 0008_dns_provider_credentials
Create Date: 2026-08-27 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009_proxy_host_locations"
down_revision: str | None = "0008_dns_provider_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "proxy_host_locations",
        sa.Column("proxy_host_id", sa.BigInteger(), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("upstream_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "forward_scheme",
            postgresql.ENUM("http", "https", name="http_scheme", create_type=False),
            server_default="http",
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["proxy_host_id"],
            ["proxy_hosts.id"],
            name=op.f("fk_proxy_host_locations_proxy_host_id_proxy_hosts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["upstream_id"],
            ["upstreams.id"],
            name=op.f("fk_proxy_host_locations_upstream_id_upstreams"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proxy_host_locations")),
        sa.UniqueConstraint(
            "proxy_host_id", "path", name="uq_proxy_host_locations_proxy_host_id_path"
        ),
    )
    op.create_index(
        op.f("ix_proxy_host_locations_proxy_host_id"),
        "proxy_host_locations",
        ["proxy_host_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_proxy_host_locations_upstream_id"),
        "proxy_host_locations",
        ["upstream_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_proxy_host_locations_upstream_id"), table_name="proxy_host_locations")
    op.drop_index(op.f("ix_proxy_host_locations_proxy_host_id"), table_name="proxy_host_locations")
    op.drop_table("proxy_host_locations")
