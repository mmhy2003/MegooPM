"""Draining Redis counters into visitor rows.

``parse_counters`` is pure; ``upsert_visitors`` is the only write. The split is
deliberate: the parsing reads untrusted input — hash fields are IP strings
supplied by whoever reached the proxy — and that is the part worth testing
exhaustively without a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.visitor_day import VisitorDay
from app.services.analytics.geoip import lookup_country


@dataclass(frozen=True, slots=True)
class VisitorCounts:
    """One visitor's activity since the previous flush — a delta, not a total."""

    ip: str
    day: date
    requests: int
    bytes: int


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes | bytearray) else str(value)


def _number(value: object) -> int | None:
    try:
        return int(_text(value))
    except (ValueError, TypeError):
        return None


def parse_counters(count_map, bytes_map, day: date) -> list[VisitorCounts]:
    """Pair the two hashes into rows, dropping anything unusable.

    Driven by the count hash: an entry present only in the bytes hash describes
    no requests and has nothing to add.
    """
    byte_totals = {_text(k): _number(v) or 0 for k, v in bytes_map.items()}
    rows: list[VisitorCounts] = []
    for raw_ip, raw_count in count_map.items():
        ip = _text(raw_ip)
        requests = _number(raw_count)
        if not ip or requests is None:
            continue
        rows.append(
            VisitorCounts(ip=ip, day=day, requests=requests, bytes=byte_totals.get(ip, 0))
        )
    return rows


async def upsert_visitors(
    session: AsyncSession, rows: list[VisitorCounts], *, now: datetime
) -> int:
    """Add these deltas to the stored totals. Returns the number of rows written."""
    if not rows:
        return 0

    table = VisitorDay.__table__
    values = [
        {
            "ip": row.ip,
            "day": row.day,
            "first_seen_at": now,
            "last_seen_at": now,
            "request_count": row.requests,
            "bytes": row.bytes,
            # Once per distinct IP per flush, never per request.
            "country": lookup_country(row.ip),
        }
        for row in rows
    ]

    stmt = pg_insert(table).values(values)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[table.c.ip, table.c.day],
            set_={
                # ADD, never replace. Each flush carries the delta since the
                # last one, so assigning would reset every visitor's count once
                # a minute — wrong numbers from code that reads as correct.
                "request_count": table.c.request_count + stmt.excluded.request_count,
                "bytes": table.c.bytes + stmt.excluded.bytes,
                "last_seen_at": stmt.excluded.last_seen_at,
                # first_seen_at is deliberately absent: it records when this
                # visitor was first seen that day and must not move.
                "country": stmt.excluded.country,
            },
        )
    )
    await session.commit()
    return len(values)


__all__ = ["VisitorCounts", "parse_counters", "upsert_visitors"]
