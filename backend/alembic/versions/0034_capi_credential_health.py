"""Remember whether CrowdSec's central API still accepts our credentials

With the community blocklist on, CrowdSec authenticates to CAPI during LAPI
init and treats a rejection as fatal. Credentials that go stale therefore sit
harmlessly in a running container until something restarts it, and then
nothing starts. The worker records the answer here so the UI can warn while
the engine is still up — which is also the only window in which it can be
repaired, since docker exec cannot reach a crash-looping container.

A status, not a job run: there is no trigger and no operation, only the last
answer and when it was given.

Revision ID: 0034_capi_credential_health
Revises: 0033_location_error_page
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0034_capi_credential_health"
down_revision: str | None = "0033_location_error_page"
branch_labels: str | None = None
depends_on: str | None = None

_TABLE = "instance_settings"


def upgrade() -> None:
    # Nullable with no default: "never checked" is a real third state, and it
    # must not be confused with "checked and fine".
    op.add_column(_TABLE, sa.Column("crowdsec_capi_status_ok", sa.Boolean(), nullable=True))
    op.add_column(_TABLE, sa.Column("crowdsec_capi_status_detail", sa.Text(), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column("crowdsec_capi_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(_TABLE, "crowdsec_capi_checked_at")
    op.drop_column(_TABLE, "crowdsec_capi_status_detail")
    op.drop_column(_TABLE, "crowdsec_capi_status_ok")
