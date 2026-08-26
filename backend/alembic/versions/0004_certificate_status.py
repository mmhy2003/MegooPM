"""MEG-19 certificate lifecycle status

Adds a ``status`` column (backed by a native ``certificate_status`` ENUM) to the
``certificates`` table so issuance/renewal can track a certificate through its
lifecycle: pending -> active, or -> failed / expired.

Reversible: ``downgrade`` drops the column and the ENUM type.

Revision ID: 0004_certificate_status
Revises: 0003_core_domain
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0004_certificate_status"
down_revision: str | None = "0003_core_domain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENUM_NAME = "certificate_status"
_ENUM_VALUES = ("pending", "active", "failed", "expired")


def upgrade() -> None:
    certificate_status = sa.Enum(*_ENUM_VALUES, name=_ENUM_NAME)
    certificate_status.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "certificates",
        sa.Column(
            "status",
            certificate_status,
            server_default="pending",
            nullable=False,
        ),
    )
    # Rows that predate this column already carry issued material — treat them
    # as active rather than pending so the sweep does not try to (re)issue them.
    op.execute("UPDATE certificates SET status = 'active'")


def downgrade() -> None:
    op.drop_column("certificates", "status")
    sa.Enum(name=_ENUM_NAME).drop(op.get_bind(), checkfirst=True)
