"""users

Adds the ``users`` table backing authentication and RBAC (see MEG-14):
Argon2-hashed passwords, an ``admin``/``member`` role, and audit timestamps.

Branches from the baseline. If concurrent domain migrations also branch from
``0001_baseline`` this produces multiple heads; resolve with ``alembic merge``.

Revision ID: 0002_users
Revises: 0001_baseline
Create Date: 2026-08-26 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_users"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Portable across Postgres (native ENUM) and SQLite (VARCHAR + CHECK).
user_role = sa.Enum("admin", "member", name="user_role")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column(
            "full_name",
            sa.String(length=255),
            server_default="",
            nullable=False,
        ),
        sa.Column(
            "role",
            user_role,
            server_default="member",
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    # Drop the ENUM type on backends that materialize one (e.g. Postgres).
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        user_role.drop(bind, checkfirst=True)
