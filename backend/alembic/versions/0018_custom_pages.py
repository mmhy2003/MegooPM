"""Custom pages — reusable HTML response bodies authored in the UI

One row per page: a name, an optional description, and the full HTML document.
Images live inside the document as base64 ``data:`` URIs, so there is no asset
table and nothing to clean up when a page is deleted.

Nothing references these rows yet. The scenarios that will serve a page (a
CrowdSec ban page, a catch-all for unmatched hosts) get their foreign keys in a
later migration; this one only creates the store.

Revision ID: 0018_custom_pages
Revises: 0017_whitelist_expressions
Create Date: 2026-09-01 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018_custom_pages"
down_revision: str | None = "0017_whitelist_expressions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "custom_pages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        # Uncapped here; the API rejects anything over 2 MiB, which is where
        # base64-embedded images start to make a page unwieldy.
        sa.Column("html", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Unique so a page can be referenced unambiguously by name once bindings land.
    op.create_unique_constraint(op.f("uq_custom_pages_name"), "custom_pages", ["name"])


def downgrade() -> None:
    op.drop_table("custom_pages")
