"""token_version on users

An integer both JWT types carry and refresh checks. Bumping it ends every
session for that user — the missing half of "I reset my password because I
think I was compromised".

Revision ID: 0025_token_version
Revises: 0024_smtp_settings
Create Date: 2026-09-03 17:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0025_token_version"
down_revision: str | None = "0024_smtp_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
