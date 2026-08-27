"""DNS-01 providers: reusable encrypted DNS provider credentials

Adds ``dns_provider_credentials``: one row per saved credential set for a
dns-lexicon provider. ``options`` holds the non-secret provider options;
``secrets_enc`` is a Fernet token wrapping the secret ones. Certificates point
at a row via ``meta.dns_credential_id`` (no FK, so history survives deletion).

Purely additive and fully reversible.

Revision ID: 0008_dns_provider_credentials
Revises: 0007_crowdsec_credentials
Create Date: 2026-08-27 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008_dns_provider_credentials"
down_revision: str | None = "0007_crowdsec_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dns_provider_credentials",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column(
            "options",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("secrets_enc", sa.Text(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dns_provider_credentials")),
        sa.UniqueConstraint("name", name=op.f("uq_dns_provider_credentials_name")),
    )


def downgrade() -> None:
    op.drop_table("dns_provider_credentials")
