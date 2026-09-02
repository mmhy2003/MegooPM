"""The most recent nginx sample from one node.

One row per node, overwritten — deliberately not history. Retaining samples
would need a pruning policy and a storage budget; the dashboard only ever shows
current state, so it stores only current state.

``requests_total`` and ``sampled_at`` are kept because they are the *previous*
sample the next write subtracts from: ``requests_per_second`` is derived at
write time, since nginx reports cumulative counters and a rate is only ever a
delta between two readings.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NodeMetrics(Base):
    """One node's latest connection counters."""

    __tablename__ = "node_metrics"

    node_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    active_connections: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requests_total: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    requests_per_second: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sampled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
