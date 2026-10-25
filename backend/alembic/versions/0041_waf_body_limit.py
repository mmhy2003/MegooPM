"""The WAF's request-body limit, and the job that applies it

CrowdSec from 1.7 on refuses any request body over 10 MB by default. The
limit and what happens over it are now set from Security → Settings and
applied by restarting CrowdSec, recorded as a crowdsec_job_run of the new
kind ``waf_apply``.

The defaults are 10 MB and ``partial`` — not CrowdSec's ``drop`` — so an
upgraded instance keeps accepting large uploads.

Revision ID: 0041_waf_body_limit
Revises: 0040_capi_blocked
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0041_waf_body_limit"
down_revision: str | None = "0040_capi_blocked"
branch_labels: str | None = None
depends_on: str | None = None

# op.add_column does NOT emit CREATE TYPE for an enum — only create_table does.
_ACTION = sa.Enum("drop", "partial", "allow", name="appsec_body_action")


def upgrade() -> None:
    _ACTION.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "instance_settings",
        sa.Column(
            "crowdsec_appsec_max_body_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="10485760",
        ),
    )
    op.add_column(
        "instance_settings",
        sa.Column("crowdsec_appsec_body_action", _ACTION, nullable=False, server_default="partial"),
    )
    # Postgres refuses ALTER TYPE ... ADD VALUE inside a transaction, and
    # Alembic wraps every migration in one. autocommit_block exists for this.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE crowdsec_job_kind ADD VALUE IF NOT EXISTS 'waf_apply'")


def downgrade() -> None:
    # Postgres cannot remove a value from an enum: the 'waf_apply' row goes,
    # the value stays in the type — a documented one-way door.
    op.execute("DELETE FROM crowdsec_job_run WHERE kind = 'waf_apply'")
    op.drop_column("instance_settings", "crowdsec_appsec_body_action")
    op.drop_column("instance_settings", "crowdsec_appsec_max_body_bytes")
    _ACTION.drop(op.get_bind(), checkfirst=True)
