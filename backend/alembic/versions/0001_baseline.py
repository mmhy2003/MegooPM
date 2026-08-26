"""baseline

Establishes the migration chain. The initial schema is intentionally empty —
feature tickets add their own tables in subsequent revisions. This exists so
``alembic upgrade head`` runs cleanly against a fresh database.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-26 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
