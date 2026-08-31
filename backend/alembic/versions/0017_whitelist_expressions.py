"""CrowdSec whitelists may match on an expr expression, not just IPs and CIDRs

Adds a ``kind`` discriminator plus the two fields an expression whitelist
needs: the optional top-level ``filter`` that scopes which events it is
evaluated against, and the ``expression`` list itself.

Both kinds still render into the same parser file, so nothing about the mount,
the boot seed or the reload path changes.

The old "must match something" check becomes kind-aware. It is still worth
having at the database level for the same reason as before: a whitelist that
matches nothing renders as valid YAML and silently does nothing.

Existing rows are all IP/CIDR whitelists and take the ``ip_cidr`` default.

Revision ID: 0017_whitelist_expressions
Revises: 0016_crowdsec_whitelists
Create Date: 2026-08-31 17:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0017_whitelist_expressions"
down_revision: str | None = "0016_crowdsec_whitelists"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Bare names: the metadata naming convention adds the ck_<table>_ prefix.
_NOT_EMPTY_CK = "not_empty"

_KIND = sa.Enum("ip_cidr", "expression", name="whitelist_kind")

# An ip_cidr whitelist needs an address to match; an expression whitelist needs
# an expression. Either way, a row that matches nothing cannot exist.
_KIND_AWARE_CHECK = (
    "(kind = 'ip_cidr' AND cardinality(ips) + cardinality(cidrs) > 0)"
    " OR (kind = 'expression' AND cardinality(expressions) > 0)"
)


def upgrade() -> None:
    # Create the type explicitly: add_column would emit it implicitly, but then
    # downgrade could not drop it cleanly.
    _KIND.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "crowdsec_whitelists",
        sa.Column("kind", _KIND, server_default="ip_cidr", nullable=False),
    )
    op.add_column(
        "crowdsec_whitelists", sa.Column("filter", sa.Text(), nullable=True)
    )
    op.add_column(
        "crowdsec_whitelists",
        sa.Column(
            "expressions",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )

    # The 0016 check rejects an expression whitelist, which has no ips/cidrs.
    op.drop_constraint(_NOT_EMPTY_CK, "crowdsec_whitelists", type_="check")
    op.create_check_constraint(_NOT_EMPTY_CK, "crowdsec_whitelists", _KIND_AWARE_CHECK)


def downgrade() -> None:
    # Expression whitelists have no ips/cidrs, so they cannot satisfy the old
    # check. Drop them explicitly rather than failing on the constraint.
    op.execute("DELETE FROM crowdsec_whitelists WHERE kind = 'expression'")

    op.drop_constraint(_NOT_EMPTY_CK, "crowdsec_whitelists", type_="check")
    op.create_check_constraint(
        _NOT_EMPTY_CK,
        "crowdsec_whitelists",
        "cardinality(ips) + cardinality(cidrs) > 0",
    )

    op.drop_column("crowdsec_whitelists", "expressions")
    op.drop_column("crowdsec_whitelists", "filter")
    op.drop_column("crowdsec_whitelists", "kind")
    _KIND.drop(op.get_bind(), checkfirst=True)
