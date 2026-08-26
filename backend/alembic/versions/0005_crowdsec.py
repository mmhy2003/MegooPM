"""MEG-22 CrowdSec per-host toggles

Adds two per-proxy-host booleans that drive the generated nginx config:

* ``crowdsec_enabled`` — wire the CrowdSec nginx bouncer into this host.
* ``crowdsec_appsec_enabled`` — additionally route requests through the inline
  AppSec/WAF component (only meaningful when the bouncer is enabled).

Both default to ``false`` so existing hosts are unchanged: the migration is
purely additive and fully reversible.

Revision ID: 0005_crowdsec
Revises: 0004_certificate_status
Create Date: 2026-08-26 21:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0005_crowdsec"
down_revision: str | None = "0004_certificate_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "proxy_hosts",
        sa.Column(
            "crowdsec_enabled",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )
    op.add_column(
        "proxy_hosts",
        sa.Column(
            "crowdsec_appsec_enabled",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("proxy_hosts", "crowdsec_appsec_enabled")
    op.drop_column("proxy_hosts", "crowdsec_enabled")
