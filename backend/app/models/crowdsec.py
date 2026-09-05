"""DB-backed CrowdSec LAPI credentials (MEG-43).

A single row (``id = 1``) holding the credentials the backend uses to talk to
the CrowdSec Local API. Storing them in the database (rather than env vars) lets
the app **self-register** on a fresh stack and rotate credentials without a
redeploy.

Secrets are encrypted at rest: ``machine_password`` and ``bouncer_key`` are
Fernet tokens (see :mod:`app.core.crypto`), never plaintext. The non-secret
``lapi_url`` / ``machine_id`` are stored in the clear so an operator can inspect
which endpoint/identity is in use.

The table is a singleton by construction — the fixed primary key doubles as the
uniqueness guarantee that makes concurrent auto-registration safe: two workers
that both try to insert the row race on the primary key, and the loser re-reads
the winner's row instead of double-registering.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# The single row that always exists once the integration is registered/seeded.
CROWDSEC_CREDENTIALS_ROW_ID = 1


class CrowdSecCredential(Base):
    """Singleton row holding the CrowdSec LAPI credentials (secrets encrypted)."""

    __tablename__ = "crowdsec_credentials"

    # Fixed to ``CROWDSEC_CREDENTIALS_ROW_ID`` — this table only ever holds one
    # row; the PK enforces that under concurrent auto-registration.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)

    # Base URL of the LAPI this identity is registered against.
    lapi_url: Mapped[str] = mapped_column(String(512), nullable=False)
    # Watcher/machine identity used for the alert read path and manual writes.
    machine_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Fernet-encrypted machine password (never plaintext).
    machine_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Fernet-encrypted bouncer API key used to read active decisions (optional:
    # when absent the backend reads decisions with the machine token instead).
    bouncer_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    # When the machine/bouncer were provisioned (self-registration or env seed).
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


__all__ = ["CROWDSEC_CREDENTIALS_ROW_ID", "CrowdSecCredential"]
