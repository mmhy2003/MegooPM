"""Remember when CrowdSec is blocking our address rather than our credentials

CrowdSec's firewall refuses a blocked address with the same "Forbidden" that
rejected credentials get, so the credential check alone told an operator on a
blocked connection to re-register — which only sends more requests from the
address being refused. The worker now asks the hub, which needs no
credentials, and records which of the two it is.

Revision ID: 0040_capi_blocked
Revises: 0039_backups
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0040_capi_blocked"
down_revision: str | None = "0039_backups"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # False until the next hourly check says otherwise: every earlier
    # rejection was recorded as a credentials problem, and stays one.
    op.add_column(
        "instance_settings",
        sa.Column(
            "crowdsec_capi_status_blocked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("instance_settings", "crowdsec_capi_status_blocked")
