"""LLM integration settings on the instance-settings singleton

Four columns: whether the feature is on, litellm's model string, an optional
API base for local runners and gateways, and the Fernet-encrypted API key.

Seeded off. Enabling by migration would make a reverse proxy's admin backend
start calling a third party because an upgrade shipped.

Revision ID: 0020_llm_settings
Revises: 0019_instance_settings
Create Date: 2026-09-01 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020_llm_settings"
down_revision: str | None = "0019_instance_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "instance_settings",
        sa.Column("llm_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("instance_settings", sa.Column("llm_model", sa.Text(), nullable=True))
    op.add_column("instance_settings", sa.Column("llm_api_base", sa.Text(), nullable=True))
    op.add_column("instance_settings", sa.Column("llm_api_key_enc", sa.Text(), nullable=True))
    # Bare name: the ck_%(table_name)s_%(constraint_name)s convention is applied
    # by alembic, so an expanded name would be double-prefixed. No constraint
    # requires a key — a local model legitimately has none.
    op.create_check_constraint(
        "llm_needs_model",
        "instance_settings",
        "llm_enabled = false OR llm_model IS NOT NULL",
    )


def downgrade() -> None:
    # The constraint goes first: dropping a column it references would fail.
    op.drop_constraint(
        op.f("ck_instance_settings_llm_needs_model"), "instance_settings", type_="check"
    )
    op.drop_column("instance_settings", "llm_api_key_enc")
    op.drop_column("instance_settings", "llm_api_base")
    op.drop_column("instance_settings", "llm_model")
    op.drop_column("instance_settings", "llm_enabled")
