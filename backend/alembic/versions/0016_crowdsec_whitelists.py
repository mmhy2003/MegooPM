"""CrowdSec whitelists authored in the UI, plus their apply state

``crowdsec_whitelists`` holds one row per rendered YAML document.
``crowdsec_whitelist_apply`` is a single row (``id=1``) recording whether the
last render actually reached CrowdSec — the apply runs in a Celery task on the
control-plane node, so it can fail long after the API returned 200, and without
this a failed reload would be invisible.

Revision ID: 0016_crowdsec_whitelists
Revises: 0015_location_forward_target
Create Date: 2026-08-31 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0016_crowdsec_whitelists"
down_revision: str | None = "0015_location_forward_target"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Bare name: the metadata naming convention (ck_%(table_name)s_%(constraint_name)s)
# is applied by alembic, so passing the expanded name would double the prefix.
_NOT_EMPTY_CK = "not_empty"


def upgrade() -> None:
    op.create_table(
        "crowdsec_whitelists",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("ips", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("cidrs", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
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
    op.create_unique_constraint(
        op.f("uq_crowdsec_whitelists_name"), "crowdsec_whitelists", ["name"]
    )
    # A whitelist matching nothing is always an operator mistake, and an empty
    # `whitelist:` block would render without complaint.
    op.create_check_constraint(
        _NOT_EMPTY_CK,
        "crowdsec_whitelists",
        "cardinality(ips) + cardinality(cidrs) > 0",
    )

    op.create_table(
        "crowdsec_whitelist_apply",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("applied_digest", sa.Text(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error", sa.Text(), nullable=True),
    )
    # Seed the singleton so readers never have to handle "no row yet".
    op.execute("INSERT INTO crowdsec_whitelist_apply (id, ok) VALUES (1, true)")


def downgrade() -> None:
    op.drop_table("crowdsec_whitelist_apply")
    op.drop_constraint(_NOT_EMPTY_CK, "crowdsec_whitelists", type_="check")
    op.drop_table("crowdsec_whitelists")
