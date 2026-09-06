"""API keys: a credential a user hands to a machine

The token is never stored — only its SHA-256 digest and a 12-character prefix.
The prefix is unique because it is the lookup handle: verifying a key must be
one indexed single-row read, not a scan.

Revision ID: 0036_api_keys
Revises: 0035_maintenance_mode
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0036_api_keys"
down_revision: str | None = "0035_maintenance_mode"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "api_key",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        # CASCADE: a deleted account must not leave working credentials behind.
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_api_key_user_id", "api_key", ["user_id"])
    op.create_index("ix_api_key_token_prefix", "api_key", ["token_prefix"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_api_key_token_prefix", table_name="api_key")
    op.drop_index("ix_api_key_user_id", table_name="api_key")
    op.drop_table("api_key")
