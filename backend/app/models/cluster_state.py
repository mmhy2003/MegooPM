"""Cluster-wide coordination state for HA deployments (MEG-35).

A single row (``id = 1``) holding the monotonically increasing
``config_version``. Every successful nginx *apply* bumps this counter inside the
same advisory-locked transaction; each node compares it to a node-local marker
to decide whether its local nginx needs a reload. This is the source of truth
for config propagation across nodes — the shared ``conf.d`` volume holds the
actual file bytes, and ``apply_config`` is idempotent, so a node that sees a
newer version simply reloads and converges.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# The single row that always exists once the cluster has applied config once.
CLUSTER_STATE_ROW_ID = 1


class ClusterState(Base):
    """Singleton row tracking the cluster's current nginx config version."""

    __tablename__ = "cluster_state"

    # Fixed to ``CLUSTER_STATE_ROW_ID`` — this table only ever holds one row.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    # Monotonic counter; bumped on every config change that reloads nginx.
    config_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    # Node that performed the most recent bump (observability only).
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


__all__ = ["ClusterState", "CLUSTER_STATE_ROW_ID"]
