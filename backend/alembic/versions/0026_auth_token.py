"""auth_token: single-use secrets bound to a user

Password reset today; invitations next. Only the hash is stored.

Revision ID: 0026_auth_token
Revises: 0025_token_version
Create Date: 2026-09-03 17:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0026_auth_token"
down_revision: str | None = "0025_token_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # create_table emits CREATE TYPE for the enum (add_column does not).
    op.create_table(
        "auth_token",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.Enum("password_reset", name="auth_token_kind"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_auth_token_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_token")),
    )
    op.create_index(op.f("ix_auth_token_token_hash"), "auth_token", ["token_hash"], unique=True)
    op.create_index(op.f("ix_auth_token_user_id"), "auth_token", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_auth_token_user_id"), table_name="auth_token")
    op.drop_index(op.f("ix_auth_token_token_hash"), table_name="auth_token")
    op.drop_table("auth_token")
    sa.Enum(name="auth_token_kind").drop(op.get_bind(), checkfirst=True)
