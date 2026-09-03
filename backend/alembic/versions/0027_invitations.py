"""invited_at on users; invitation kind on auth_token

Revision ID: 0027_invitations
Revises: 0026_auth_token
Create Date: 2026-09-03 19:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0027_invitations"
down_revision: str | None = "0026_auth_token"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Postgres refuses ALTER TYPE ... ADD VALUE inside a transaction, and
    # Alembic wraps every migration in one. autocommit_block exists for this.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE auth_token_kind ADD VALUE IF NOT EXISTS 'invitation'")


def downgrade() -> None:
    # Postgres cannot remove a value from an enum. Rows of the new kind must
    # go first, and the value stays in the type — a documented one-way door.
    op.execute("DELETE FROM auth_token WHERE kind = 'invitation'")
    op.drop_column("users", "invited_at")
