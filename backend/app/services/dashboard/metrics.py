"""Recording and aggregating the per-node nginx samples.

Pure except for :func:`record_sample`, which needs the row it replaces in order
to compute a rate. Everything the traffic card displays is derived here, so the
endpoint stays a thin caller and the arithmetic is testable without nginx or a
database.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.node_metrics import NodeMetrics
from app.services.nginx.stub_status import StubStatus


@dataclass(frozen=True, slots=True)
class TrafficTotals:
    """Totals across live nodes.

    ``None`` means *not measured*, which is not the same as zero: no node has
    reported recently, so the connection count is unknown rather than idle.
    """

    active_connections: int | None
    requests_per_second: float | None
    reporting_nodes: int
    stale_nodes: int


def compute_rate(
    sample: StubStatus, previous: tuple[int, datetime] | None, *, now: datetime
) -> float:
    """Requests per second between the previous sample and this one.

    Returns ``0.0`` rather than a negative or infinite figure for the cases that
    would otherwise produce nonsense on the card: no previous sample, a counter
    reset (nginx restarted), and no time elapsed between the two readings.
    """
    if previous is None:
        return 0.0
    previous_total, previous_at = previous
    elapsed = (now - previous_at).total_seconds()
    if elapsed <= 0:
        return 0.0
    delta = sample.requests - previous_total
    if delta < 0:
        return 0.0
    return delta / elapsed


async def record_sample(
    session: AsyncSession, node_id: str, sample: StubStatus, *, now: datetime
) -> None:
    """Upsert this node's row, deriving the rate from the row it replaces."""
    row = await session.get(NodeMetrics, node_id)
    previous = (int(row.requests_total), row.sampled_at) if row is not None else None
    rate = compute_rate(sample, previous, now=now)

    if row is None:
        session.add(
            NodeMetrics(
                node_id=node_id,
                active_connections=sample.active,
                requests_total=sample.requests,
                requests_per_second=rate,
                sampled_at=now,
            )
        )
    else:
        row.active_connections = sample.active
        row.requests_total = sample.requests
        row.requests_per_second = rate
        row.sampled_at = now
    await session.commit()


def aggregate(rows: Iterable, *, now: datetime, stale_after: float) -> TrafficTotals:
    """Sum the live nodes. Stale rows are excluded, never counted as zero."""
    materialised = list(rows)
    live = [
        row
        for row in materialised
        if (now - row.sampled_at).total_seconds() <= stale_after
    ]
    stale = len(materialised) - len(live)
    if not live:
        return TrafficTotals(None, None, 0, stale)
    return TrafficTotals(
        active_connections=sum(row.active_connections for row in live),
        requests_per_second=round(sum(row.requests_per_second for row in live), 2),
        reporting_nodes=len(live),
        stale_nodes=stale,
    )


async def load_traffic(
    session: AsyncSession, *, now: datetime, stale_after: float
) -> TrafficTotals:
    """Read every node's latest sample and aggregate it."""
    rows = (await session.scalars(select(NodeMetrics))).all()
    return aggregate(rows, now=now, stale_after=stale_after)


__all__ = [
    "TrafficTotals",
    "aggregate",
    "compute_rate",
    "load_traffic",
    "record_sample",
]
