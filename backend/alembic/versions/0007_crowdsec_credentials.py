"""MEG-43 CrowdSec DB-backed credentials

Adds the singleton ``crowdsec_credentials`` table that holds the LAPI
credentials the backend uses (and auto-registers into) instead of reading them
from environment variables. Secret columns (``machine_password_enc``,
``bouncer_key_enc``) store Fernet ciphertext, never plaintext.

Purely additive and fully reversible; no data is migrated by the DDL itself
(the env→DB seed happens at runtime on first use).

Revision ID: 0007_crowdsec_credentials
Revises: 0006_cluster_state
Create Date: 2026-08-27 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_crowdsec_credentials"
down_revision: str | None = "0006_cluster_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "crowdsec_credentials",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("lapi_url", sa.String(length=512), nullable=False),
        sa.Column("machine_id", sa.String(length=255), nullable=True),
        sa.Column("machine_password_enc", sa.Text(), nullable=True),
        sa.Column("bouncer_key_enc", sa.Text(), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("crowdsec_credentials")
