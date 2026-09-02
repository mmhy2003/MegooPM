# Visitor Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record which IPs and countries reach the managed hosts, as one row per `(ip, day)`, so the dashboard can list visitors.

**Architecture:** `log_by_lua` increments Redis counters on every request; a leader-locked Celery task drains them into Postgres once a minute, resolving country from a bundled MMDB as it goes. Redis does the aggregation, so millions of requests become thousands of upserts.

**Tech Stack:** OpenResty + `lua-resty-redis` (already in the image), Redis (already running), Python 3.12, SQLAlchemy 2.0 async, Alembic, Celery, `maxminddb` (new), pytest; Next.js 16, vitest.

**Spec:** `docs/superpowers/specs/2026-09-02-visitor-analytics-design.md`

## Global Constraints

- **The log phase cannot open a socket.** Cosockets are unavailable in `log_by_lua` — verified in the image, where a handler logs its first line and is then aborted at `ngx.socket.tcp()`. Counting therefore goes to a `lua_shared_dict` and a timer started from `init_worker_by_lua` drains it. Anything that tries to reach Redis from the request path will silently count nothing.
- **Losing analytics must never cost a served request.** With the shared-memory design this is structural: the request path does no I/O. `pcall` stays as a second line so a fault cannot fail an already-sent response.
- **The flush upsert ADDS, it does not replace.** Each flush carries the delta since the last one. `SET request_count = EXCLUDED.request_count` would silently reset every visitor's count once a minute — code that looks right and produces wrong numbers forever.
- **The flush MUST hold `leader_lock`.** HA requires a *shared* Redis (`docker-compose.ha.yml`: "REDIS_URL is required (shared Redis)"), so every node sees the same counters. Without the lock, every count is multiplied by the cluster size.
- **Drain by removing the fields that were read, never by deleting the key.** `DEL` would discard increments that arrived during the flush.
- **The Lua and the flush MUST agree on the date.** The Lua builds its key from UTC (`os.date("!%Y-%m-%d")`), not `ngx.today()`, because the flush task uses `datetime.now(UTC)`. A local-time container clock would otherwise write counters to a key nothing drains, and the data would vanish at TTL with no error anywhere.
- **Do not count MegooPM's own traffic.** The dashboard scrapes `stub_status` every 15s and the healthcheck hits `/healthz`; both would otherwise appear as a very busy visitor. Skip the status port and `/healthz` in the Lua.
- **Retention is required, not optional.** These rows are visitor IP addresses — personal data. The prune task ships in the same plan as the writer, never "later".
- Run backend tests in a Linux container — the app imports `fcntl`:

```bash
export MSYS_NO_PATHCONV=1
docker network create megoopm-testnet
docker run -d --name megoopm-testdb --network megoopm-testnet \
  -e POSTGRES_USER=megoopm -e POSTGRES_PASSWORD=megoopm -e POSTGRES_DB=megoopm postgres:16-alpine
docker run -d --name megoopm-test --user root --network megoopm-testnet \
  -v "C:/Projects/megoopm/backend:/src" -w /src \
  -e CELERY_TASK_ALWAYS_EAGER=true -e CELERY_RESULT_BACKEND=cache+memory:// \
  -e DATABASE_URL="postgresql+asyncpg://megoopm:megoopm@megoopm-testdb:5432/megoopm" \
  --entrypoint sleep megoopm-backend infinity
docker exec megoopm-test pip install -q "pytest>=8.2" "pytest-asyncio>=0.23" "aiosqlite>=0.20" "ruff>=0.6" "maxminddb>=2.6"
```

  Do NOT mount the working tree over `/app` — it shadows the entrypoint with the host's CRLF copy. After any `alembic upgrade` against this database, reset it before running the suite: `docker exec megoopm-testdb psql -U megoopm -d megoopm -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"`.
- Adding response fields breaks frontend fixtures. `vitest` will not catch it; `npm run typecheck` will.

---

### Task 1: The table

**Files:**
- Create: `backend/app/models/visitor_day.py`
- Create: `backend/alembic/versions/0023_visitor_day.py`
- Modify: `backend/app/models/__init__.py`

**Interfaces:**
- Produces: `VisitorDay` with primary key `(ip, day)` and columns `first_seen_at`, `last_seen_at`, `request_count`, `bytes`, `country`.

- [x] **Step 1: Add the model**

Create `backend/app/models/visitor_day.py`:

```python
"""One row per distinct visitor IP per day.

Aggregated, not per-request: at 100 req/s a proxy produces ~8.6 million
requests a day, and this table grows with *visitors* instead — thousands of
rows rather than millions.

Bucketed by day so "who visited in the last 24 hours" is answerable and so
pruning is a single DELETE. The counters are summed across flushes and across
nodes, which is why the writer's upsert adds rather than replaces.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, String, func
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VisitorDay(Base):
    __tablename__ = "visitor_day"

    # INET rather than text: Postgres validates it, indexes it well, and makes
    # future subnet queries possible without a migration.
    ip: Mapped[str] = mapped_column(INET, primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    request_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Null when the address could not be located: an unlocatable visitor is
    # still a visitor, so the lookup must never drop the row.
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
```

Register it in `app/models/__init__.py` beside `NodeMetrics`, and add `"VisitorDay"` to `__all__`.

- [x] **Step 2: Write the migration**

Create `backend/alembic/versions/0023_visitor_day.py`:

```python
"""Per-IP-per-day visitor aggregates

Revision ID: 0023_visitor_day
Revises: 0022_node_metrics
Create Date: 2026-09-02 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_visitor_day"
down_revision: str | None = "0022_node_metrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "visitor_day",
        sa.Column("ip", postgresql.INET(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("request_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.PrimaryKeyConstraint("ip", "day", name=op.f("pk_visitor_day")),
    )
    # The prune deletes by day and the dashboard reads recent days; both scan on
    # `day` alone, which the composite primary key (ip first) cannot serve.
    op.create_index(op.f("ix_visitor_day_day"), "visitor_day", ["day"])


def downgrade() -> None:
    op.drop_index(op.f("ix_visitor_day_day"), "visitor_day")
    op.drop_table("visitor_day")
```

- [x] **Step 3: Run the migration up, down and up**

```bash
docker exec megoopm-test sh -c "alembic upgrade head && alembic downgrade -1 && alembic upgrade head"
docker exec megoopm-testdb psql -U megoopm -d megoopm -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
```

Expected: all three succeed. Reset afterwards — see Global Constraints.

- [x] **Step 4: Commit**

```bash
git add backend/app/models backend/alembic
git commit -m "feat(analytics): per-IP-per-day visitor table"
```

---

### Task 2: The GeoIP reader

**Files:**
- Create: `backend/app/services/analytics/__init__.py`, `backend/app/services/analytics/geoip.py`
- Modify: `backend/pyproject.toml`, `backend/app/core/config.py`, `backend/Dockerfile`
- Test: `backend/tests/test_geoip.py` (create)

**Interfaces:**
- Produces: `lookup_country(ip: str) -> str | None` and `database_available() -> bool`.

- [x] **Step 1: Add the dependency and setting**

In `backend/pyproject.toml`, beside the other data dependencies:

```
    # MMDB reader for the bundled country database (visitor analytics). Pure
    # Python, no C extension, no network.
    "maxminddb>=2.6",
```

In `backend/app/core/config.py`:

```python
    # Country lookup for visitor analytics. The image bundles a DB-IP country
    # database here; an operator who prefers MaxMind points this at their own
    # file. Missing file means country resolution is simply off.
    geoip_database_path: str = "/app/data/dbip-country-lite.mmdb"
    # Days of visitor rows kept. These are IP addresses — personal data — so
    # this is a retention limit, not a performance tuning knob.
    visitor_retention_days: int = 30
```

- [x] **Step 2: Bundle the database, without making the build depend on it**

In `backend/Dockerfile`, before the app is copied:

```dockerfile
# DB-IP IP-to-Country Lite (CC BY 4.0) for visitor analytics. Best-effort: the
# URL carries the current month and a build running on the 1st may find it not
# yet published, so a failure leaves the file absent rather than breaking the
# build. Country resolution degrades to null and is logged once at startup.
RUN mkdir -p /app/data \
    && MONTH="$(date -u +%Y-%m)" \
    && (curl -fsSL "https://download.db-ip.com/free/dbip-country-lite-${MONTH}.mmdb.gz" \
        | gunzip > /app/data/dbip-country-lite.mmdb \
        || echo "db-ip download failed; country resolution will be disabled") \
    && ls -l /app/data/ || true
```

Attribution belongs in the docs: DB-IP Lite is CC BY 4.0 and requires it. Add a line to `README.md` crediting "IP Geolocation by DB-IP" with a link.

- [x] **Step 3: Write the failing tests**

Create `backend/tests/test_geoip.py`:

```python
"""The country lookup.

Pure apart from reading a file. The rule under test is that a failed lookup
returns None rather than raising: an unlocatable visitor is still a visitor,
and a lookup that throws would abort a whole flush batch.
"""

from __future__ import annotations

from app.services.analytics.geoip import lookup_country


def test_a_private_address_has_no_country() -> None:
    assert lookup_country("10.0.0.1") is None


def test_a_malformed_address_returns_none_rather_than_raising() -> None:
    """It arrives from a Redis hash field; a bad value must not abort the batch."""
    assert lookup_country("not-an-ip") is None
    assert lookup_country("") is None


def test_a_missing_database_disables_lookups_without_raising(monkeypatch) -> None:
    from app.services.analytics import geoip

    monkeypatch.setattr(geoip.settings, "geoip_database_path", "/nonexistent.mmdb")
    geoip.reset_reader()
    assert lookup_country("8.8.8.8") is None
    assert geoip.database_available() is False
```

- [x] **Step 4: Run the tests to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_geoip.py -p no:cacheprovider
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.analytics'`.

- [x] **Step 5: Write the reader**

Create `backend/app/services/analytics/__init__.py` (a docstring only) and
`backend/app/services/analytics/geoip.py`:

```python
"""Country lookup against a bundled MMDB database.

The reader is opened once and reused: opening it per lookup would re-read the
file thousands of times a flush. Every failure path returns None, because this
runs inside a batch — one bad address must not cost the whole minute's data.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import maxminddb

from app.core.config import settings

log = logging.getLogger(__name__)

_lock = threading.Lock()
_reader: maxminddb.Reader | None = None
_tried = False


def reset_reader() -> None:
    """Drop the cached reader. For tests that change the configured path."""
    global _reader, _tried
    with _lock:
        if _reader is not None:
            _reader.close()
        _reader = None
        _tried = False


def _get_reader() -> maxminddb.Reader | None:
    global _reader, _tried
    with _lock:
        if _reader is not None or _tried:
            return _reader
        _tried = True
        path = Path(settings.geoip_database_path)
        if not path.exists():
            # Once, not per lookup: the build may legitimately have shipped
            # without the database, and this should be a note rather than noise.
            log.warning(
                "GeoIP database missing at %s; country resolution disabled", path
            )
            return None
        try:
            _reader = maxminddb.open_database(str(path))
        except Exception as exc:  # noqa: BLE001 - a corrupt file must not crash
            log.warning("GeoIP database unreadable: %s", exc)
            return None
        return _reader


def database_available() -> bool:
    return _get_reader() is not None


def lookup_country(ip: str) -> str | None:
    """ISO-3166 alpha-2 for ``ip``, or None if it cannot be determined."""
    reader = _get_reader()
    if reader is None or not ip:
        return None
    try:
        record = reader.get(ip)
    except (ValueError, TypeError):
        # Not a valid address. It came from a Redis hash field, so it is not
        # trusted input.
        return None
    if not isinstance(record, dict):
        return None
    country = record.get("country") or record.get("registered_country") or {}
    code = country.get("iso_code") if isinstance(country, dict) else None
    return code if isinstance(code, str) else None


__all__ = ["database_available", "lookup_country", "reset_reader"]
```

- [x] **Step 6: Run the tests to verify they pass**

```bash
docker exec megoopm-test python -m pytest tests/test_geoip.py -p no:cacheprovider
```

Expected: PASS, 3 tests. They pass whether or not the database shipped, which is deliberate — the suite must not depend on a build-time download.

- [x] **Step 7: Commit**

```bash
git add backend/pyproject.toml backend/Dockerfile backend/app/core/config.py backend/app/services/analytics backend/tests/test_geoip.py README.md
git commit -m "feat(analytics): country lookup from a bundled MMDB"
```

---

### Task 3: The flush

**Files:**
- Create: `backend/app/services/analytics/flush.py`
- Test: `backend/tests/test_analytics_flush.py` (create)

**Interfaces:**
- Consumes: `lookup_country` (Task 2), `VisitorDay` (Task 1).
- Produces:
  - `VisitorCounts(ip: str, day: date, requests: int, bytes: int)`
  - `parse_counters(count_map, bytes_map, day) -> list[VisitorCounts]`
  - `async upsert_visitors(session, rows, *, now) -> int`

- [x] **Step 1: Write the failing tests**

Create `backend/tests/test_analytics_flush.py`:

```python
"""Turning Redis counters into rows.

`parse_counters` is pure and carries the risk: it reads untrusted hash fields
(anything can connect to the proxy) and produces the rows a database write is
built from.
"""

from __future__ import annotations

from datetime import date

from app.services.analytics.flush import parse_counters

DAY = date(2026, 9, 2)


def test_pairs_counts_with_bytes() -> None:
    rows = parse_counters({"1.2.3.4": "10"}, {"1.2.3.4": "2048"}, DAY)
    assert len(rows) == 1
    assert rows[0].ip == "1.2.3.4"
    assert rows[0].requests == 10
    assert rows[0].bytes == 2048
    assert rows[0].day == DAY


def test_an_ip_with_no_byte_counter_still_produces_a_row() -> None:
    """The two hashes are written by separate commands, so a crash between them
    can leave one behind. Losing the visitor entirely would be worse."""
    rows = parse_counters({"1.2.3.4": "10"}, {}, DAY)
    assert rows[0].requests == 10
    assert rows[0].bytes == 0


def test_a_non_numeric_counter_is_skipped_not_fatal() -> None:
    """Hash fields come from request data; one bad value must not cost the
    whole batch."""
    rows = parse_counters({"1.2.3.4": "abc", "5.6.7.8": "3"}, {}, DAY)
    assert [r.ip for r in rows] == ["5.6.7.8"]


def test_bytes_are_accepted_as_a_string_or_bytes() -> None:
    """redis-py returns bytes unless decode_responses is set; accept both so a
    client-config change cannot silently zero every counter."""
    rows = parse_counters({b"1.2.3.4": b"7"}, {b"1.2.3.4": b"14"}, DAY)
    assert rows[0].ip == "1.2.3.4"
    assert rows[0].requests == 7
    assert rows[0].bytes == 14


def test_no_counters_is_an_empty_list() -> None:
    assert parse_counters({}, {}, DAY) == []
```

- [x] **Step 2: Run the tests to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_analytics_flush.py -p no:cacheprovider
```

Expected: FAIL — no module `app.services.analytics.flush`.

- [x] **Step 3: Write the parser and the writer**

Create `backend/app/services/analytics/flush.py`:

```python
"""Draining Redis counters into visitor rows.

`parse_counters` is pure; `upsert_visitors` is the only write. The split exists
because the parsing reads untrusted input — hash fields are attacker-controlled
IP strings — and that is exactly the part worth testing exhaustively.
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
    ip: str
    day: date
    requests: int
    bytes: int


def _text(value) -> str:
    return value.decode() if isinstance(value, bytes | bytearray) else str(value)


def _number(value) -> int | None:
    try:
        return int(_text(value))
    except (ValueError, TypeError):
        return None


def parse_counters(count_map, bytes_map, day: date) -> list[VisitorCounts]:
    """Pair the two hashes into rows, dropping anything unusable.

    Driven by the count hash: an entry present only in the bytes hash has no
    request count and nothing to add.
    """
    byte_totals = {_text(k): _number(v) or 0 for k, v in bytes_map.items()}
    rows: list[VisitorCounts] = []
    for raw_ip, raw_count in count_map.items():
        ip = _text(raw_ip)
        requests = _number(raw_count)
        if not ip or requests is None:
            continue
        rows.append(
            VisitorCounts(
                ip=ip, day=day, requests=requests, bytes=byte_totals.get(ip, 0)
            )
        )
    return rows


async def upsert_visitors(
    session: AsyncSession, rows: list[VisitorCounts], *, now: datetime
) -> int:
    """Add these deltas to the stored totals. Returns rows written."""
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
            # Once per distinct IP per flush, not per request.
            "country": lookup_country(row.ip),
        }
        for row in rows
    ]

    stmt = pg_insert(table).values(values)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[table.c.ip, table.c.day],
            set_={
                # ADD, never replace: each flush carries the delta since the
                # last one, so assigning would reset every visitor's count once
                # a minute — wrong numbers from code that reads as correct.
                "request_count": table.c.request_count + stmt.excluded.request_count,
                "bytes": table.c.bytes + stmt.excluded.bytes,
                "last_seen_at": stmt.excluded.last_seen_at,
                # first_seen_at is deliberately not updated.
                "country": stmt.excluded.country,
            },
        )
    )
    await session.commit()
    return len(values)


__all__ = ["VisitorCounts", "parse_counters", "upsert_visitors"]
```

- [x] **Step 4: Run the tests to verify they pass**

```bash
docker exec megoopm-test python -m pytest tests/test_analytics_flush.py -p no:cacheprovider
```

Expected: PASS, 5 tests.

- [x] **Step 5: Prove the upsert adds, against real Postgres**

This is the constraint most likely to be got wrong, and a pure test cannot
check it. Create `backend/tests/test_analytics_flush_pg.py` with the
`pg_session` fixture copied from `tests/test_ban_page_loader_pg.py`, and:

```python
async def test_two_flushes_of_the_same_visitor_add_up(pg_session) -> None:
    # The bug this guards: a replacing upsert resets every count once a minute.
    rows = [VisitorCounts(ip="1.2.3.4", day=DAY, requests=10, bytes=100)]
    await upsert_visitors(pg_session, rows, now=NOW)
    await upsert_visitors(pg_session, rows, now=LATER)

    stored = (await pg_session.scalars(select(VisitorDay))).all()
    assert len(stored) == 1
    assert stored[0].request_count == 20
    assert stored[0].bytes == 200


async def test_first_seen_is_not_moved_by_a_later_flush(pg_session) -> None:
    await upsert_visitors(
        pg_session, [VisitorCounts("1.2.3.4", DAY, 1, 1)], now=NOW
    )
    await upsert_visitors(
        pg_session, [VisitorCounts("1.2.3.4", DAY, 1, 1)], now=LATER
    )
    stored = (await pg_session.scalars(select(VisitorDay))).all()
    assert stored[0].first_seen_at == NOW
    assert stored[0].last_seen_at == LATER


async def test_the_same_ip_on_two_days_is_two_rows(pg_session) -> None:
    await upsert_visitors(pg_session, [VisitorCounts("1.2.3.4", DAY, 1, 1)], now=NOW)
    await upsert_visitors(
        pg_session, [VisitorCounts("1.2.3.4", OTHER_DAY, 1, 1)], now=NOW
    )
    assert len((await pg_session.scalars(select(VisitorDay))).all()) == 2
```

Define `DAY`, `OTHER_DAY`, `NOW`, `LATER` at module level as timezone-aware
values.

- [x] **Step 6: Run and commit**

```bash
docker exec megoopm-test python -m pytest tests/test_analytics_flush_pg.py -p no:cacheprovider
docker exec megoopm-test ruff check app tests alembic
git add backend/app/services/analytics/flush.py backend/tests/test_analytics_flush.py backend/tests/test_analytics_flush_pg.py
git commit -m "feat(analytics): drain Redis counters into visitor rows"
```

---

### Task 4: The tasks

**Files:**
- Create: `backend/app/tasks/analytics.py`
- Modify: `backend/app/core/celery_app.py`, `backend/app/core/config.py`
- Test: `backend/tests/test_analytics_tasks.py` (create)

**Interfaces:**
- Produces: `app.tasks.analytics.flush_visitor_counters`, `app.tasks.analytics.prune_visitor_days`, and `settings.visitor_flush_interval_seconds` (default `60.0`), `settings.visitor_redis_prefix` (default `"megoopm:visits"`).

- [x] **Step 1: Write the failing tests**

Create `backend/tests/test_analytics_tasks.py`. The Redis client is stubbed;
what matters is the drain semantics:

```python
async def test_the_drain_removes_only_the_fields_it_read(monkeypatch) -> None:
    """Deleting the key would discard increments that arrived mid-flush."""
    from app.tasks import analytics

    deleted: list[tuple[str, tuple[str, ...]]] = []

    class FakeRedis:
        async def hgetall(self, key):
            return {"1.2.3.4": "5"} if key.endswith("count:2026-09-02") else {}

        async def hdel(self, key, *fields):
            deleted.append((key, fields))

        async def aclose(self):
            return None

    monkeypatch.setattr(analytics, "_redis", lambda: FakeRedis())
    monkeypatch.setattr(analytics, "upsert_visitors", _noop_upsert)

    await analytics._flush_async(day=date(2026, 9, 2), session_factory=_null_factory())

    assert deleted, "fields must be removed after a successful write"
    assert all(fields == ("1.2.3.4",) for _, fields in deleted)


async def test_nothing_is_removed_when_the_write_fails(monkeypatch) -> None:
    """Removing first would lose the batch if the database write then failed."""
    ...
    assert deleted == []


def test_the_flush_holds_the_leader_lock() -> None:
    """A shared Redis means every node sees the same counters; without the lock
    each node would upsert them and multiply every count by the cluster size."""
    import inspect

    from app.tasks import analytics

    assert "leader_lock" in inspect.getsource(analytics.flush_visitor_counters)


def test_both_tasks_are_scheduled() -> None:
    from app.core.celery_app import celery_app

    tasks = {e["task"] for e in celery_app.conf.beat_schedule.values()}
    assert "app.tasks.analytics.flush_visitor_counters" in tasks
    assert "app.tasks.analytics.prune_visitor_days" in tasks
```

- [x] **Step 2: Run to verify they fail, then write the tasks**

```bash
docker exec megoopm-test python -m pytest tests/test_analytics_tasks.py -p no:cacheprovider
```

Then create `backend/app/tasks/analytics.py`. Structure:

```python
async def _flush_async(*, day: date, session_factory=None) -> int:
    """Drain one day's counters. Write first, remove second.

    If the database write fails the fields stay in Redis and the next flush
    picks them up. The reverse order would lose a minute of data whenever
    Postgres hiccuped.
    """
    client = _redis()
    count_key = f"{settings.visitor_redis_prefix}:count:{day.isoformat()}"
    bytes_key = f"{settings.visitor_redis_prefix}:bytes:{day.isoformat()}"
    try:
        counts = await client.hgetall(count_key)
        if not counts:
            return 0
        byte_totals = await client.hgetall(bytes_key)
        rows = parse_counters(counts, byte_totals, day)
        if not rows:
            return 0

        if session_factory is None:
            engine = create_async_engine(settings.database_url)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            written = await upsert_visitors(session, rows, now=datetime.now(UTC))

        # Only the fields just written, never the whole key: increments that
        # arrived during the write must survive.
        fields = [row.ip for row in rows]
        await client.hdel(count_key, *fields)
        await client.hdel(bytes_key, *fields)
        return written
    finally:
        await client.aclose()


@celery_app.task(name="app.tasks.analytics.flush_visitor_counters")
def flush_visitor_counters() -> None:
    """Cluster-wide singleton: HA shares one Redis, so exactly one node drains."""
    engine = create_engine(settings.sync_database_url)
    with leader_lock(engine, "visitor-flush") as acquired:
        if not acquired:
            return
        # Yesterday too: a flush that spans midnight would otherwise strand the
        # previous day's final counters until they expired.
        today = datetime.now(UTC).date()
        for day in (today, today - timedelta(days=1)):
            asyncio.run(_flush_async(day=day))
```

The prune is written out in full rather than described, because it is the one
step that deletes data and an off-by-one in the cutoff silently destroys a day
of it:

```python
async def _prune_async(*, today: date, session_factory=None) -> int:
    """Delete visitor rows older than the retention window.

    The cutoff is INCLUSIVE of the retention window: with a 30-day setting,
    today and the previous 29 days are kept. Using `today - days` without the
    +1 would silently drop a day more than configured, which nobody would
    notice until an audit asked how long the data is kept.
    """
    cutoff = today - timedelta(days=settings.visitor_retention_days - 1)
    if session_factory is None:
        engine = create_async_engine(settings.database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        result = await session.execute(
            delete(VisitorDay).where(VisitorDay.day < cutoff)
        )
        await session.commit()
        return int(result.rowcount or 0)


@celery_app.task(name="app.tasks.analytics.prune_visitor_days")
def prune_visitor_days() -> None:
    """Enforce the retention limit. Cluster-wide singleton: one node deletes."""
    engine = create_engine(settings.sync_database_url)
    with leader_lock(engine, "visitor-prune") as acquired:
        if not acquired:
            return
        removed = asyncio.run(_prune_async(today=datetime.now(UTC).date()))
        log.info("pruned %s visitor rows past the retention window", removed)
```

and a test pinning the boundary, since the cutoff is the whole point:

```python
async def test_retention_keeps_exactly_the_configured_window(pg_session) -> None:
    # 30 days means today plus the previous 29, not the previous 30.
    monkeypatch.setattr(settings, "visitor_retention_days", 30)
    ...
    assert kept_days == 30
```

Check `leader_lock`'s real signature in `app/services/cluster/locks.py` and how
existing callers build the sync engine — copy that, do not invent it.

- [x] **Step 3: Schedule both**

In `app/core/celery_app.py`, add to `beat_schedule`:

```python
        "flush-visitor-counters": {
            "task": "app.tasks.analytics.flush_visitor_counters",
            "schedule": settings.visitor_flush_interval_seconds,
            "options": {"expires": settings.visitor_flush_interval_seconds},
        },
        "prune-visitor-days": {
            "task": "app.tasks.analytics.prune_visitor_days",
            "schedule": crontab(hour=3, minute=30),
        },
```

**No `task_routes` entry.** Unlike the metrics scrape, this is deliberately *not*
node-local: the counters are in a shared Redis, so any node may drain them and
`leader_lock` decides which does.

- [x] **Step 4: Run everything and commit**

```bash
docker exec megoopm-test python -m pytest -p no:cacheprovider
docker exec megoopm-test ruff check app tests alembic
git add backend/app/tasks/analytics.py backend/app/core backend/tests/test_analytics_tasks.py
git commit -m "feat(analytics): flush and prune visitor counters"
```

---

### Task 5: The nginx side

**Files:**
- Create: `infra/nginx/lua/megoopm_analytics.lua`
- Modify: `infra/nginx/nginx.conf`, `infra/nginx/docker-entrypoint.sh`, `docker-compose.yml`

**Interfaces:**
- Consumes: the Redis key layout from Task 4 (`{prefix}:count:{YYYY-MM-DD}`).

- [x] **Step 1: Generate the Lua config from the environment**

In `infra/nginx/docker-entrypoint.sh`, beside the logging block, parse
`REDIS_URL` into a Lua table:

```sh
# --- Visitor analytics (/etc/nginx/lua/megoopm_analytics_conf.lua) ---
# The log-phase handler needs Redis coordinates. A generated Lua file rather
# than a parsed .conf: nothing to tokenise, and a syntax error fails loudly at
# init instead of silently disabling counting.
: "${REDIS_URL:=}"
: "${ANALYTICS_ENABLED:=true}"
_redis_hostport="$(echo "${REDIS_URL}" | sed -e 's|^redis://||' -e 's|/.*$||')"
_redis_host="$(echo "${_redis_hostport}" | cut -d: -f1)"
_redis_port="$(echo "${_redis_hostport}" | cut -s -d: -f2)"
{
    echo "-- Generated by docker-entrypoint.sh -- do not edit."
    echo "return {"
    if [ -n "${_redis_host}" ] && [ "${ANALYTICS_ENABLED}" = "true" ]; then
        echo "  enabled = true,"
    else
        echo "  enabled = false,"
    fi
    echo "  host = \"${_redis_host:-redis}\","
    echo "  port = ${_redis_port:-6379},"
    echo "  prefix = \"megoopm:visits\","
    echo "  ttl = 172800,"
    echo "  status_port = \"8081\","
    echo "}"
} > /etc/nginx/lua/megoopm_analytics_conf.lua
```

Add `REDIS_URL` to the `nginx` service's environment in `docker-compose.yml`
(and the HA file), since nginx does not currently receive it.

- [x] **Step 2: Write the log handler**

Create `infra/nginx/lua/megoopm_analytics.lua`:

```lua
-- Visitor analytics: two Redis counters per request.
--
-- Runs in the log phase, AFTER the response has been sent, so it adds no
-- client-visible latency. It can still occupy a worker, which is why every
-- Redis call has a short timeout AND the whole body is wrapped by the caller
-- in pcall: losing analytics must never cost a served request.
--
-- Aggregating in Redis is the point. A busy proxy does millions of requests a
-- day; this turns them into two O(1) increments each, and the backend later
-- writes one row per distinct visitor.

local conf = require("megoopm_analytics_conf")

local M = {}

local function counters()
    local redis = require("resty.redis")
    local red = redis:new()
    -- Short: a hung Redis holds this worker for exactly this long.
    red:set_timeouts(200, 200, 200)

    local ok, err = red:connect(conf.host, conf.port)
    if not ok then
        return nil, err
    end
    return red
end

function M.log()
    if not conf.enabled then
        return
    end

    -- Never count MegooPM's own traffic: the dashboard scrapes stub_status
    -- every 15s and the healthcheck hits /healthz, which would otherwise make
    -- the instance itself the busiest visitor it has.
    if ngx.var.server_port == conf.status_port then
        return
    end
    if ngx.var.uri == "/healthz" then
        return
    end

    -- remote_addr, not the header: real_ip has already rewritten this from the
    -- trusted proxy ranges, and reading the header directly would let anyone
    -- forge their own address.
    local ip = ngx.var.remote_addr
    if not ip or ip == "" then
        return
    end

    local red, err = counters()
    if not red then
        ngx.log(ngx.DEBUG, "[megoopm] analytics: redis unavailable: ", tostring(err))
        return
    end

    -- UTC, NOT ngx.today(): that returns the container's LOCAL date, while
    -- the flush task builds its key from datetime.now(UTC). Under any non-UTC
    -- clock the two would disagree for part of each day, and counters would be
    -- written to a key nothing ever drains -- silently, until the TTL ate them.
    local day = os.date("!%Y-%m-%d")
    local count_key = conf.prefix .. ":count:" .. day
    local bytes_key = conf.prefix .. ":bytes:" .. day

    red:init_pipeline()
    red:hincrby(count_key, ip, 1)
    red:hincrby(bytes_key, ip, tonumber(ngx.var.bytes_sent) or 0)
    red:expire(count_key, conf.ttl)
    red:expire(bytes_key, conf.ttl)
    red:commit_pipeline()

    -- Back to the pool rather than closed: a new connection per request would
    -- cost more than the counting.
    red:set_keepalive(10000, 32)
end

return M
```

- [x] **Step 3: Wire it into nginx**

In `infra/nginx/nginx.conf`, in the `http {}` block near the other Lua:

```nginx
    # Visitor analytics. Wrapped in pcall so a fault here can never fail a
    # request that has already been served.
    log_by_lua_block {
        local ok, mod = pcall(require, "megoopm_analytics")
        if ok then
            pcall(mod.log)
        end
    }
```

- [x] **Step 4: Prove it counts, and prove it degrades**

The second half matters more than the first:

```bash
export MSYS_NO_PATHCONV=1
docker network create megoopm-lua-probe
docker run -d --name probe-redis --network megoopm-lua-probe redis:7-alpine
docker run --rm --network megoopm-lua-probe --entrypoint sh \
  -v "/c/Projects/megoopm/infra/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" \
  -v "/c/Projects/megoopm/infra/nginx/lua/megoopm_analytics.lua:/etc/nginx/lua/megoopm_analytics.lua:ro" \
  megoopm-nginx:latest -c '
mkdir -p /data/nginx/conf.d /data/nginx/default /var/empty/megoopm
echo "access_log /dev/null;" > /etc/nginx/logging.conf
printf "return { enabled = true, host = \"probe-redis\", port = 6379, prefix = \"megoopm:visits\", ttl = 3600, status_port = \"8081\" }\n" > /etc/nginx/lua/megoopm_analytics_conf.lua
cat > /data/nginx/conf.d/megoopm-probe.conf <<EOF
server { listen 8090; location / { return 200 "ok\n"; } }
EOF
openresty -p /usr/local/openresty/nginx -c /etc/nginx/nginx.conf
sleep 1
for i in 1 2 3; do curl -s -o /dev/null http://127.0.0.1:8090/; done
sleep 1
echo "--- counted ---"
'
docker exec probe-redis redis-cli --scan --pattern "megoopm:visits:*"
docker exec probe-redis redis-cli HGETALL "megoopm:visits:count:$(date -u +%F)"
```

Expected: a hash with the client IP and the value `3`.

Then the degradation check — **the one that matters**:

```bash
docker stop probe-redis
# re-run the nginx container and curl it again
```

Expected: the request still returns `200 ok`. If it hangs or errors, stop: the
timeout or the pcall is wrong, and analytics is costing served requests.

Tear down: `docker rm -f probe-redis && docker network rm megoopm-lua-probe`.

- [x] **Step 5: Check line endings and commit**

```bash
git ls-files --eol infra/nginx/lua/megoopm_analytics.lua infra/nginx/docker-entrypoint.sh
```

A CRLF Lua file or entrypoint breaks in the container. Then:

```bash
git add infra/nginx docker-compose.yml docker-compose.ha.yml
git commit -m "feat(analytics): count visitors from nginx's log phase"
```

---

### Task 6: Show the visitors

**Files:**
- Modify: `backend/app/schemas/dashboard.py`, `backend/app/services/dashboard/summary.py`, `backend/app/api/routes/dashboard.py`
- Create: `frontend/src/components/dashboard/visitors-card.tsx` and its test
- Modify: `frontend/src/components/dashboard/dashboard-view.tsx`, `frontend/src/lib/api/resources/dashboard.ts`

**Interfaces:**
- Produces: `GET /api/v1/dashboard/visitors?days=1` returning `VisitorSummary { total_visitors, total_requests, countries: [{country, visitors, requests}], top_ips: [{ip, country, requests, last_seen_at}] }`.

- [x] **Step 1: Write the failing backend test**

Add to `backend/tests/test_dashboard_api.py`:

```python
async def test_visitors_is_empty_before_any_traffic(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    body = (await client.get("/api/v1/dashboard/visitors", headers=auth)).json()
    assert body["total_visitors"] == 0
    assert body["countries"] == []
    assert body["top_ips"] == []


async def test_visitors_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/dashboard/visitors")).status_code in (401, 403)
```

- [x] **Step 2: Implement the endpoint**

Create `backend/app/services/dashboard/visitors.py`:

```python
async def load_visitors(
    db: AsyncSession, *, days: int, top: int = 20
) -> VisitorSummary:
    """Aggregate the retained rows over the last ``days`` days.

    The window is inclusive of today: days=1 means today only.
    """
    since = datetime.now(UTC).date() - timedelta(days=days - 1)
    window = VisitorDay.day >= since

    totals = (
        await db.execute(
            select(
                func.count(func.distinct(VisitorDay.ip)),
                func.coalesce(func.sum(VisitorDay.request_count), 0),
            ).where(window)
        )
    ).one()

    # Countries: nulls are excluded from this list because "unknown" cannot be
    # shown on a map or ranked meaningfully -- but they remain in the totals
    # above, so the numbers still add up to every visitor seen.
    country_rows = (
        await db.execute(
            select(
                VisitorDay.country,
                func.count(func.distinct(VisitorDay.ip)),
                func.sum(VisitorDay.request_count),
            )
            .where(window, VisitorDay.country.is_not(None))
            .group_by(VisitorDay.country)
            .order_by(func.sum(VisitorDay.request_count).desc(), VisitorDay.country)
        )
    ).all()

    # Top IPs: summed across days, so a visitor active all week ranks above one
    # busy for an hour.
    ip_rows = (
        await db.execute(
            select(
                VisitorDay.ip,
                func.max(VisitorDay.country),
                func.sum(VisitorDay.request_count).label("requests"),
                func.max(VisitorDay.last_seen_at),
            )
            .where(window)
            .group_by(VisitorDay.ip)
            .order_by(func.sum(VisitorDay.request_count).desc(), VisitorDay.ip)
            .limit(top)
        )
    ).all()

    return VisitorSummary(
        total_visitors=int(totals[0] or 0),
        total_requests=int(totals[1] or 0),
        countries=[
            CountryCount(country=c, visitors=int(v), requests=int(r))
            for c, v, r in country_rows
        ],
        top_ips=[
            VisitorRow(
                ip=str(ip), country=country, requests=int(requests), last_seen_at=seen
            )
            for ip, country, requests, seen in ip_rows
        ],
    )
```

Add the route with a `days` query parameter bounded to `1..settings.visitor_retention_days`,
so a caller cannot ask for a window the data cannot cover.

- [x] **Step 3: Write the failing card test**

Create `frontend/src/components/dashboard/visitors-card.test.tsx`:

```tsx
it("says no visitors recorded rather than showing zeros", () => {
  render(<VisitorsCard visitors={{ total_visitors: 0, total_requests: 0, countries: [], top_ips: [] }} />);
  expect(screen.getByText(/no visitors recorded/i)).toBeInTheDocument();
});

it("lists countries by request volume", () => {
  render(<VisitorsCard visitors={{ total_visitors: 9, total_requests: 400, countries: [{ country: "DE", visitors: 5, requests: 300 }], top_ips: [] }} />);
  expect(screen.getByText("DE")).toBeInTheDocument();
  expect(screen.getByText(/300/)).toBeInTheDocument();
});

it("shows an unlocated visitor rather than hiding it", () => {
  render(<VisitorsCard visitors={{ total_visitors: 1, total_requests: 2, countries: [], top_ips: [{ ip: "1.2.3.4", country: null, requests: 2, last_seen_at: "2026-09-02T00:00:00Z" }] }} />);
  expect(screen.getByText("1.2.3.4")).toBeInTheDocument();
});
```

- [x] **Step 4: Build the card and mount it**

Follow `cards.tsx`: the same `Card` shell, the same "absent is not zero" rule.
Mount it in `dashboard-view.tsx` below the grid, beside the threat map, and add
`visitors()` to the API client.

- [x] **Step 5: Run the full gates and commit**

```bash
docker exec megoopm-test python -m pytest -p no:cacheprovider
docker exec megoopm-test python -m scripts.export_openapi
docker exec megoopm-test python -m pytest -p no:cacheprovider
cd frontend && npm run gen:api && npx vitest run && npm run typecheck && npm run lint && npm run build
```

```bash
git add backend frontend
git commit -m "feat(dashboard): show recorded visitors and countries"
```

---

## Manual verification

Not reachable by any automated test:

1. Bring the stack up, visit a managed host a few times from another machine.
2. `docker compose exec redis redis-cli HGETALL "megoopm:visits:count:$(date -u +%F)"`
   — expect your IP with a count.
3. Wait 60s, then check the visitors card shows you, with a country.
4. `docker compose stop redis`, then request the host again. **It must still
   serve normally.** This is the constraint the whole design rests on.
5. `docker compose exec db psql -U megoopm -c "select * from visitor_day"` —
   confirm counts accumulate across several flushes rather than resetting.
6. Confirm the dashboard's own polling does not appear as a visitor.


---

## Executed 2026-09-02

All six tasks complete. Backend **794 passed, 41 skipped**, ruff clean.
Frontend **425 passed, 1 skipped**, typecheck, lint and build clean.

**The ingestion design changed, because the specced one cannot work.**
Cosockets are unavailable in `log_by_lua`, so the planned direct Redis call was
aborted every time and counted nothing — found by running it, not by reading.
The log phase now writes to a `lua_shared_dict` and a timer in worker 0 drains
it. The Redis key layout is identical, so Tasks 1-4 needed no change.

Verified against the real image:

| check | result |
| --- | --- |
| five requests to a managed host | exactly five counts in Redis |
| `stub_status` scrape and `/healthz` | correctly excluded |
| requests with Redis stopped | **200 OK in 0.5 ms** |

That last row is the one that matters: the original design would have cost a
200 ms timeout on every request while Redis was away.
