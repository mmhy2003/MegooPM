"""Static sites: serve a folder of plain files under the sites mount

Each row names a ``folder`` under ``settings.sites_root`` (read-only /sites).
The check constraint repeats the API's folder rule, so no value can point
nginx outside the mount or at a hidden folder whatever wrote the row.

Revision ID: 0042_static_sites
Revises: 0041_waf_body_limit
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042_static_sites"
down_revision: str | None = "0041_waf_body_limit"
branch_labels: str | None = None
depends_on: str | None = None

_FOLDER_CHECK = (
    "folder ~ '^[A-Za-z0-9_-][A-Za-z0-9._-]*(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$' "
    "AND length(folder) <= 255"
)


def upgrade() -> None:
    op.create_table(
        "static_sites",
        sa.Column(
            "domain_names",
            postgresql.ARRAY(sa.String(length=255)),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("folder", sa.String(length=255), nullable=False),
        sa.Column("certificate_id", sa.BigInteger(), nullable=True),
        sa.Column("access_list_id", sa.BigInteger(), nullable=True),
        sa.Column("ssl_forced", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("http2_support", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("hsts_enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("hsts_subdomains", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("block_exploits", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("crowdsec_enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("advanced_config", sa.Text(), server_default="", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
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
        sa.CheckConstraint(_FOLDER_CHECK, name=op.f("ck_static_sites_folder")),
        sa.ForeignKeyConstraint(
            ["certificate_id"],
            ["certificates.id"],
            name=op.f("fk_static_sites_certificate_id_certificates"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["access_list_id"],
            ["access_lists.id"],
            name=op.f("fk_static_sites_access_list_id_access_lists"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_static_sites")),
    )
    op.create_index(
        op.f("ix_static_sites_certificate_id"), "static_sites", ["certificate_id"], unique=False
    )
    op.create_index(
        op.f("ix_static_sites_access_list_id"), "static_sites", ["access_list_id"], unique=False
    )
    op.create_index(op.f("ix_static_sites_enabled"), "static_sites", ["enabled"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_static_sites_enabled"), table_name="static_sites")
    op.drop_index(op.f("ix_static_sites_access_list_id"), table_name="static_sites")
    op.drop_index(op.f("ix_static_sites_certificate_id"), table_name="static_sites")
    op.drop_table("static_sites")
