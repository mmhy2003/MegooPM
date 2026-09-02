# Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An instance dashboard, first in the sidebar, showing certificate health, config/cluster health, security activity, live traffic, and a globe of where attacks originate — built only on data the instance already has.

**Architecture:** One aggregate endpoint feeds the scalar cards; a second, separately failing endpoint feeds the globe from CrowdSec alerts. Live connections are per-node by construction, so each node's beat scrapes its own nginx `stub_status` and upserts a row keyed by `node_id`, mirroring how `cluster_node` already records `applied_version`.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 (async), Alembic, Celery + Redis, FastAPI, pytest; Next.js 16 (App Router), React 19, base-ui, vitest; nginx/OpenResty.

**Spec:** `docs/superpowers/specs/2026-09-02-dashboard-design.md`

## Global Constraints

- **The scrape task MUST be routed to the local node's queue in HA.** Every node consumes one queue named for its own `NODE_ID` (`_configure_ha` in `app/core/celery_app.py`). Without a `task_routes` entry, node A's beat tick can be executed by node B's worker, which would scrape *B's* nginx and upsert *B's* row — so A is never measured and B is measured twice. Copy the pattern `reconcile_local_nginx` already uses.
- **`stub_status` must NOT be bound to `127.0.0.1`.** The scraper runs in a different container. It listens on `:8081` with no `ports:` mapping — private by not being published, exactly as the reload agent on `:9099` already is.
- **Reuse `settings.node_liveness_window_seconds`** for staleness. A second definition of "this node is gone" on the same page is a bug waiting to happen.
- **A failing source degrades its own card, never the page.** CrowdSec being unreachable must not stop certificate expiry rendering. Each group in the payload is independently nullable, and a card with no data must say so rather than render `0` — "0 active bans" and "CrowdSec is down" mean opposite things.
- **Stale node metrics are excluded from totals, never counted as zero.** A node that stopped reporting has unknown connections, not none.
- Run backend tests in a Linux container — the app imports `fcntl`. Start it once, with Postgres attached so the DB-gated suites do not silently skip:

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
docker exec megoopm-test pip install -q "pytest>=8.2" "pytest-asyncio>=0.23" "aiosqlite>=0.20" "ruff>=0.6"
```

  Do NOT mount the working tree over `/app`: it shadows the image's entrypoint with the host's CRLF copy and the container dies on `bash\r`. If you run `alembic upgrade head` against this database, reset it before running the suite — the seeded rows collide with the test fixtures:
  `docker exec megoopm-testdb psql -U megoopm -d megoopm -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"`.
  Tear down with `docker rm -f megoopm-test megoopm-testdb && docker network rm megoopm-testnet`.
- Changing the API schema breaks `tests/test_openapi.py::test_committed_openapi_is_in_sync`. Refresh with `docker exec megoopm-test python -m scripts.export_openapi`, then `cd frontend && npm run gen:api`.
- Adding fields to a response schema breaks every frontend fixture that constructs one. `vitest` will not catch it; `npm run typecheck` will.

---

### Task 1: Expose and parse `stub_status`

**Files:**
- Modify: `infra/nginx/nginx.conf`
- Create: `backend/app/services/nginx/stub_status.py`
- Test: `backend/tests/test_stub_status.py` (create)

**Interfaces:**
- Produces: `parse_stub_status(text: str) -> StubStatus` and `StubStatus(active, accepted, handled, requests)` in `app/services/nginx/stub_status.py`.

- [x] **Step 1: Add the status server to nginx**

In `infra/nginx/nginx.conf`, after the existing `default_server` block and before `include /data/nginx/conf.d/*.conf;`:

```nginx
    # Connection counters for the dashboard, scraped by this node's worker.
    #
    # Deliberately NOT bound to 127.0.0.1: the scraper runs in a different
    # container and reaches this as `nginx:8081`. It is private by having no
    # `ports:` mapping, the same posture the reload agent on :9099 has.
    #
    # Only /stub_status answers; everything else 404s, so anything that reaches
    # the compose network learns connection counts and nothing more.
    server {
        listen 8081;
        server_name _;
        root /var/empty/megoopm;

        location = /stub_status {
            stub_status;
            access_log off;
        }
    }
```

- [x] **Step 2: Verify nginx loads and the endpoint answers**

```bash
export MSYS_NO_PATHCONV=1
docker run --rm --entrypoint sh \
  -v "/c/Projects/megoopm/infra/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" \
  megoopm-nginx:latest -c '
mkdir -p /data/nginx/conf.d /data/nginx/default /var/empty/megoopm
echo "access_log /dev/null;" > /etc/nginx/logging.conf
openresty -p /usr/local/openresty/nginx -c /etc/nginx/nginx.conf
sleep 1
echo "--- body ---"; curl -s http://127.0.0.1:8081/stub_status
echo "--- other path ---"; curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8081/anything
'
```

Expected: the `Active connections:` body, then `404`. Record the exact body — Step 3's fixture must be the real thing, not a remembered format.

- [x] **Step 3: Write the failing tests**

Create `backend/tests/test_stub_status.py`:

```python
"""Tests for the nginx stub_status parser.

Pure: no nginx, no network. The format is fixed and ancient, but a
misparse silently reports wrong numbers on the dashboard rather than
failing, so it is worth pinning exactly.
"""

from __future__ import annotations

import pytest
from app.services.nginx.stub_status import ParseError, parse_stub_status

BODY = """Active connections: 43
server accepts handled requests
 1204 1204 9001
Reading: 0 Writing: 5 Waiting: 38
"""


def test_parses_the_documented_format() -> None:
    got = parse_stub_status(BODY)
    assert got.active == 43
    assert got.accepted == 1204
    assert got.handled == 1204
    assert got.requests == 9001


def test_rejects_a_body_that_is_not_stub_status() -> None:
    """An nginx error page is HTML; parsing it as numbers would report noise
    as a connection count."""
    with pytest.raises(ParseError):
        parse_stub_status("<html><body>404 Not Found</body></html>")


def test_rejects_a_truncated_body() -> None:
    with pytest.raises(ParseError):
        parse_stub_status("Active connections: 43\n")


def test_tolerates_extra_whitespace() -> None:
    got = parse_stub_status(
        "Active connections:   7 \nserver accepts handled requests\n   1 2 3 \n"
    )
    assert (got.active, got.accepted, got.handled, got.requests) == (7, 1, 2, 3)
```

- [x] **Step 4: Run the tests to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_stub_status.py -p no:cacheprovider
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.nginx.stub_status'`.

- [x] **Step 5: Write the parser**

Create `backend/app/services/nginx/stub_status.py`:

```python
"""Parser for nginx's ``stub_status`` body.

Pure: no network. The scrape is elsewhere; this only turns text into numbers,
which is what makes the counter arithmetic testable without nginx.

The format is fixed::

    Active connections: 43
    server accepts handled requests
     1204 1204 9001
    Reading: 0 Writing: 5 Waiting: 38
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ACTIVE = re.compile(r"Active connections:\s+(\d+)")
_COUNTERS = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s*$", re.MULTILINE)


class ParseError(ValueError):
    """The body was not a stub_status page.

    Raised rather than returning zeros: an error page parsed as "0 connections"
    would be indistinguishable from a genuinely idle server, and the dashboard
    would report a lie instead of an outage.
    """


@dataclass(frozen=True, slots=True)
class StubStatus:
    """One sample. ``accepted``/``handled``/``requests`` are cumulative since
    the worker started, so a rate is a delta between two samples."""

    active: int
    accepted: int
    handled: int
    requests: int


def parse_stub_status(text: str) -> StubStatus:
    active = _ACTIVE.search(text)
    counters = _COUNTERS.search(text)
    if active is None or counters is None:
        raise ParseError("not a stub_status body")
    return StubStatus(
        active=int(active.group(1)),
        accepted=int(counters.group(1)),
        handled=int(counters.group(2)),
        requests=int(counters.group(3)),
    )


__all__ = ["ParseError", "StubStatus", "parse_stub_status"]
```

- [x] **Step 6: Run the tests to verify they pass**

```bash
docker exec megoopm-test python -m pytest tests/test_stub_status.py -p no:cacheprovider
```

Expected: PASS, 4 tests.

- [x] **Step 7: Commit**

```bash
git add infra/nginx/nginx.conf backend/app/services/nginx/stub_status.py backend/tests/test_stub_status.py
git commit -m "feat(nginx): expose stub_status and parse it"
```

---

### Task 2: Store per-node samples

**Files:**
- Create: `backend/app/models/node_metrics.py`
- Create: `backend/alembic/versions/0022_node_metrics.py`
- Create: `backend/app/services/dashboard/metrics.py`
- Test: `backend/tests/test_node_metrics.py` (create)

**Interfaces:**
- Consumes: `StubStatus` from Task 1.
- Produces:
  - `NodeMetrics` model (`node_id` PK, `active_connections`, `requests_total`, `requests_per_second`, `sampled_at`)
  - `record_sample(session, node_id, sample, *, now) -> None`
  - `aggregate(rows, *, now, stale_after) -> TrafficTotals(active_connections, requests_per_second, reporting_nodes, stale_nodes)`

- [x] **Step 1: Add the model**

Create `backend/app/models/node_metrics.py`:

```python
"""The most recent nginx sample from one node.

One row per node, overwritten — deliberately not history. Retaining samples
would need a pruning policy and a storage budget; the dashboard only ever shows
current state, so it stores only current state.

``requests_per_second`` is computed at write time from the previous row, which
is why the row keeps ``requests_total`` and ``sampled_at``: they are the
previous sample the next write subtracts from.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NodeMetrics(Base):
    __tablename__ = "node_metrics"

    node_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    active_connections: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requests_total: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    requests_per_second: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sampled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

Register it wherever the other models are imported for metadata (check `app/db/base.py` or `app/models/__init__.py` and follow what `cluster_state.py` does).

- [x] **Step 2: Write the migration**

Create `backend/alembic/versions/0022_node_metrics.py`:

```python
"""Most recent nginx sample per node, for the dashboard's traffic card

One row per node, overwritten on each scrape. No enum columns here, so unlike
0021 there is no type to create by hand.

Revision ID: 0022_node_metrics
Revises: 0021_crowdsec_ban_page
Create Date: 2026-09-02 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_node_metrics"
down_revision: str | None = "0021_crowdsec_ban_page"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "node_metrics",
        sa.Column("node_id", sa.String(length=255), nullable=False),
        sa.Column("active_connections", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requests_total", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "requests_per_second", sa.Float(), nullable=False, server_default="0"
        ),
        sa.Column(
            "sampled_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("node_id", name=op.f("pk_node_metrics")),
    )


def downgrade() -> None:
    op.drop_table("node_metrics")
```

- [x] **Step 3: Run the migration up, down, and up**

```bash
docker exec megoopm-test sh -c "alembic upgrade head && alembic downgrade -1 && alembic upgrade head"
docker exec megoopm-testdb psql -U megoopm -d megoopm -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
```

Expected: all three succeed. The reset afterwards matters — see Global Constraints.

- [x] **Step 4: Write the failing tests**

Create `backend/tests/test_node_metrics.py`:

```python
"""Recording and aggregating per-node nginx samples.

The rate arithmetic and the staleness rule are pure and carry the risk: a
mistake here reports a wrong number confidently rather than failing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.dashboard.metrics import aggregate
from app.services.nginx.stub_status import StubStatus

NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


class _Row:
    """A stand-in for NodeMetrics; aggregate() only reads attributes."""

    def __init__(self, node_id, active, rps, sampled_at):
        self.node_id = node_id
        self.active_connections = active
        self.requests_per_second = rps
        self.sampled_at = sampled_at


def test_totals_sum_across_reporting_nodes() -> None:
    rows = [
        _Row("a", 10, 2.0, NOW - timedelta(seconds=5)),
        _Row("b", 7, 1.5, NOW - timedelta(seconds=5)),
    ]
    got = aggregate(rows, now=NOW, stale_after=60)
    assert got.active_connections == 17
    assert got.requests_per_second == 3.5
    assert got.reporting_nodes == 2
    assert got.stale_nodes == 0


def test_a_stale_node_is_excluded_not_counted_as_zero() -> None:
    """It has unknown connections, not none — counting it as zero would make a
    dead node look like an idle one."""
    rows = [
        _Row("a", 10, 2.0, NOW - timedelta(seconds=5)),
        _Row("b", 999, 99.0, NOW - timedelta(seconds=600)),
    ]
    got = aggregate(rows, now=NOW, stale_after=60)
    assert got.active_connections == 10
    assert got.reporting_nodes == 1
    assert got.stale_nodes == 1


def test_a_node_exactly_at_the_cutoff_is_still_live() -> None:
    rows = [_Row("a", 4, 1.0, NOW - timedelta(seconds=60))]
    got = aggregate(rows, now=NOW, stale_after=60)
    assert got.reporting_nodes == 1


def test_no_rows_reports_nothing_rather_than_zero() -> None:
    """Before any scrape has run there is no measurement; the card must be able
    to say so instead of claiming the server is idle."""
    got = aggregate([], now=NOW, stale_after=60)
    assert got.reporting_nodes == 0
    assert got.active_connections is None
```

Then, in the same file, the rate arithmetic:

```python
def test_rate_is_the_delta_over_elapsed_time() -> None:
    from app.services.dashboard.metrics import compute_rate

    previous = (1000, NOW - timedelta(seconds=10))
    assert compute_rate(StubStatus(1, 0, 0, 1100), previous, now=NOW) == 10.0


def test_a_counter_reset_reports_zero_not_a_negative_rate() -> None:
    """nginx restarting zeroes the counters. Subtracting gives a negative rate,
    which would render as a nonsensical figure."""
    from app.services.dashboard.metrics import compute_rate

    previous = (9000, NOW - timedelta(seconds=10))
    assert compute_rate(StubStatus(1, 0, 0, 5), previous, now=NOW) == 0.0


def test_the_first_sample_has_no_rate_yet() -> None:
    from app.services.dashboard.metrics import compute_rate

    assert compute_rate(StubStatus(1, 0, 0, 500), None, now=NOW) == 0.0


def test_two_samples_at_the_same_instant_do_not_divide_by_zero() -> None:
    from app.services.dashboard.metrics import compute_rate

    assert compute_rate(StubStatus(1, 0, 0, 600), (500, NOW), now=NOW) == 0.0
```

- [x] **Step 5: Run the tests to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_node_metrics.py -p no:cacheprovider
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.dashboard'`.

- [x] **Step 6: Write the implementation**

Create `backend/app/services/dashboard/__init__.py` (empty) and
`backend/app/services/dashboard/metrics.py`:

```python
"""Recording and aggregating the per-node nginx samples.

Pure except for ``record_sample``, which needs the previous row to compute a
rate. Everything the dashboard displays is derived here, so the endpoint stays
a thin caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.node_metrics import NodeMetrics
from app.services.nginx.stub_status import StubStatus


@dataclass(frozen=True, slots=True)
class TrafficTotals:
    """``None`` means "not measured", which is not the same as zero."""

    active_connections: int | None
    requests_per_second: float | None
    reporting_nodes: int
    stale_nodes: int


def compute_rate(
    sample: StubStatus, previous: tuple[int, datetime] | None, *, now: datetime
) -> float:
    """Requests per second between the previous sample and this one.

    Returns 0.0 rather than a negative or infinite figure for the three cases
    that would otherwise produce nonsense: no previous sample, a counter reset
    (nginx restarted), and two samples with no time between them.
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


def aggregate(rows, *, now: datetime, stale_after: float) -> TrafficTotals:
    """Sum the live nodes. Stale rows are excluded, never counted as zero."""
    live = [r for r in rows if (now - r.sampled_at).total_seconds() <= stale_after]
    stale = len(list(rows)) - len(live)
    if not live:
        return TrafficTotals(None, None, 0, stale)
    return TrafficTotals(
        active_connections=sum(r.active_connections for r in live),
        requests_per_second=round(sum(r.requests_per_second for r in live), 2),
        reporting_nodes=len(live),
        stale_nodes=stale,
    )


async def load_traffic(
    session: AsyncSession, *, now: datetime, stale_after: float
) -> TrafficTotals:
    rows = (await session.scalars(select(NodeMetrics))).all()
    return aggregate(rows, now=now, stale_after=stale_after)


__all__ = ["TrafficTotals", "aggregate", "compute_rate", "load_traffic", "record_sample"]
```

- [x] **Step 7: Run the tests to verify they pass**

```bash
docker exec megoopm-test python -m pytest tests/test_node_metrics.py -p no:cacheprovider
```

Expected: PASS, 8 tests.

- [x] **Step 8: Commit**

```bash
git add backend/app/models/node_metrics.py backend/alembic/versions/0022_node_metrics.py backend/app/services/dashboard backend/tests/test_node_metrics.py
git commit -m "feat(dashboard): record and aggregate per-node nginx samples"
```

---

### Task 3: Scrape on each node's beat

**Files:**
- Create: `backend/app/tasks/metrics.py`
- Modify: `backend/app/core/celery_app.py`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/test_metrics_task.py` (create)

**Interfaces:**
- Consumes: `parse_stub_status` (Task 1), `record_sample` (Task 2).
- Produces: the Celery task `app.tasks.metrics.scrape_local_nginx`, and `settings.metrics_scrape_interval_seconds` (default `15.0`), `settings.nginx_status_url` (default `http://nginx:8081/stub_status`).

- [x] **Step 1: Add the settings**

In `backend/app/core/config.py`, beside the other nginx settings:

```python
    # The dashboard's traffic card. The URL is this node's OWN nginx: each
    # backend reaches only its co-located container, which is why the sample is
    # stored per node rather than aggregated at scrape time.
    nginx_status_url: str = "http://nginx:8081/stub_status"
    metrics_scrape_interval_seconds: float = 15.0
```

- [x] **Step 2: Write the failing test**

Create `backend/tests/test_metrics_task.py`:

```python
"""The per-node scrape task.

The HTTP call is stubbed; what is worth testing is that a failed scrape does
not write a misleading row, since a wrong number is worse than a missing one.
"""

from __future__ import annotations

import pytest
from app.services.nginx.stub_status import ParseError

pytestmark = pytest.mark.asyncio

BODY = "Active connections: 3\nserver accepts handled requests\n 10 10 40\n"


async def test_a_successful_scrape_records_a_sample(monkeypatch) -> None:
    from app.tasks import metrics

    recorded = {}

    async def fake_fetch(url: str) -> str:
        return BODY

    async def fake_record(session, node_id, sample, *, now):
        recorded["node_id"] = node_id
        recorded["active"] = sample.active

    monkeypatch.setattr(metrics, "_fetch", fake_fetch)
    monkeypatch.setattr(metrics, "record_sample", fake_record)

    await metrics._scrape_async(session_factory=_null_session_factory())

    assert recorded["active"] == 3


async def test_an_unreachable_nginx_records_nothing(monkeypatch) -> None:
    """Leaving the previous row is right: it goes stale on its own and drops
    out of the totals. Writing zeros would report the node as idle."""
    from app.tasks import metrics

    async def boom(url: str) -> str:
        raise OSError("connection refused")

    called = False

    async def fake_record(*a, **kw):
        nonlocal called
        called = True

    monkeypatch.setattr(metrics, "_fetch", boom)
    monkeypatch.setattr(metrics, "record_sample", fake_record)

    await metrics._scrape_async(session_factory=_null_session_factory())

    assert called is False


async def test_a_garbage_body_records_nothing(monkeypatch) -> None:
    from app.tasks import metrics

    async def html(url: str) -> str:
        return "<html>404</html>"

    called = False

    async def fake_record(*a, **kw):
        nonlocal called
        called = True

    monkeypatch.setattr(metrics, "_fetch", html)
    monkeypatch.setattr(metrics, "record_sample", fake_record)

    await metrics._scrape_async(session_factory=_null_session_factory())

    assert called is False
```

Add a `_null_session_factory()` helper at the top of the file that yields an object supporting `async with` and returning a dummy session — the session is never used once `record_sample` is stubbed:

```python
from contextlib import asynccontextmanager


def _null_session_factory():
    @asynccontextmanager
    async def factory():
        yield object()

    return factory
```

- [x] **Step 3: Run the test to verify it fails**

```bash
docker exec megoopm-test python -m pytest tests/test_metrics_task.py -p no:cacheprovider
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.tasks.metrics'`.

- [x] **Step 4: Write the task**

Create `backend/app/tasks/metrics.py`, following the session pattern in `app/tasks/certs.py` (Celery runs outside FastAPI's session scope, so the task opens its own engine):

```python
"""Scrape this node's nginx and store the sample.

Runs on every node's beat. In HA the task is routed to the node's OWN queue
(see ``_configure_ha``), because a tick executed on another node would scrape
that node's nginx and upsert its row — leaving this node unmeasured.

A failed scrape writes nothing. The previous row then ages out of the totals on
the staleness rule, which is the honest outcome: unknown, not zero.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings
from app.services.dashboard.metrics import record_sample
from app.services.nginx.stub_status import ParseError, parse_stub_status

log = logging.getLogger(__name__)


async def _fetch(url: str) -> str:
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


async def _scrape_async(*, session_factory=None) -> None:
    try:
        body = await _fetch(settings.nginx_status_url)
        sample = parse_stub_status(body)
    except (OSError, ParseError, httpx.HTTPError) as exc:
        # Debug, not warning: a node whose nginx is briefly down would otherwise
        # fill the log every 15 seconds.
        log.debug("stub_status scrape failed: %s", exc)
        return

    if session_factory is None:
        engine = create_async_engine(settings.database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        await record_sample(
            session, settings.effective_node_id, sample, now=datetime.now(UTC)
        )


@celery_app.task(name="app.tasks.metrics.scrape_local_nginx")
def scrape_local_nginx() -> None:
    asyncio.run(_scrape_async())
```

- [x] **Step 5: Schedule it, and route it in HA**

In `backend/app/core/celery_app.py`, add to `celery_app.conf.beat_schedule`:

```python
        "scrape-nginx-metrics": {
            "task": "app.tasks.metrics.scrape_local_nginx",
            "schedule": settings.metrics_scrape_interval_seconds,
            # A tick that could not run promptly is worthless: the next one is
            # 15 seconds away and carries fresher numbers.
            "options": {"expires": settings.metrics_scrape_interval_seconds},
        },
```

and inside `_configure_ha`, extend `task_routes`:

```python
    celery_app.conf.task_routes = {
        "app.tasks.nginx.reconcile_local_nginx": {"queue": own_queue},
        # Same reason: a scrape must run on the node whose nginx it measures.
        "app.tasks.metrics.scrape_local_nginx": {"queue": own_queue},
    }
```

- [x] **Step 6: Run the tests**

```bash
docker exec megoopm-test python -m pytest -p no:cacheprovider
docker exec megoopm-test ruff check app tests alembic
```

Expected: all pass, ruff clean.

- [x] **Step 7: Commit**

```bash
git add backend/app/tasks/metrics.py backend/app/core/celery_app.py backend/app/core/config.py backend/tests/test_metrics_task.py
git commit -m "feat(dashboard): scrape each node's own nginx on its beat"
```

---

### Task 4: The summary endpoint

**Files:**
- Create: `backend/app/services/dashboard/summary.py`
- Create: `backend/app/schemas/dashboard.py`
- Create: `backend/app/api/routes/dashboard.py`
- Modify: `backend/app/api/routes/__init__.py` (register the router — follow how `cluster` is registered)
- Modify: `backend/app/api/routes/cluster.py` (extract the status computation)
- Create: `backend/app/services/cluster/status.py`
- Test: `backend/tests/test_dashboard_api.py` (create)

**Interfaces:**
- Consumes: `load_traffic` (Task 2).
- Produces: `GET /api/v1/dashboard/summary` returning `DashboardSummary`.

- [x] **Step 1: Extract the cluster status computation**

Move the body of `cluster_status` from `backend/app/api/routes/cluster.py:29-61` into a new
`backend/app/services/cluster/status.py` as:

```python
async def compute_cluster_status(db: AsyncSession) -> ClusterStatus:
```

with the code unchanged. Then have the route call it:

```python
@router.get("/status", response_model=ClusterStatus)
async def cluster_status(_admin: AdminUser, db: SessionDep) -> ClusterStatus:
    """Report the shared config version and how far each node has converged."""
    return await compute_cluster_status(db)
```

This is what lets the dashboard reuse it rather than recompute it. Two
implementations of "is the cluster converged" that could disagree is worse than
either being wrong alone.

Run `docker exec megoopm-test python -m pytest tests/test_cluster_coordination.py -p no:cacheprovider` and confirm it still passes before going further — a pure move must change no behaviour.

- [x] **Step 2: Write the failing tests**

Create `backend/tests/test_dashboard_api.py`, using the `client` and `auth` fixtures the same way `tests/test_settings_api.py` does:

```python
async def test_summary_requires_admin(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/dashboard/summary")).status_code in (401, 403)


async def test_summary_counts_hosts_and_certificates(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    body = (await client.get("/api/v1/dashboard/summary", headers=auth)).json()
    assert "certificates" in body
    assert "inventory" in body
    assert body["inventory"]["proxy_hosts_total"] == 0


async def test_summary_reports_traffic_as_unmeasured_before_any_scrape(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    """Not zero: nothing has measured this instance yet, and the card has to be
    able to say so."""
    body = (await client.get("/api/v1/dashboard/summary", headers=auth)).json()
    assert body["traffic"]["reporting_nodes"] == 0
    assert body["traffic"]["active_connections"] is None


async def test_summary_survives_crowdsec_being_unreachable(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    """The certificate card is the one that matters most; a CrowdSec outage
    must not take the page down with it."""
    resp = await client.get("/api/v1/dashboard/summary", headers=auth)
    assert resp.status_code == 200
    body = resp.json()
    assert body["security"] is None or isinstance(body["security"], dict)
    assert body["certificates"] is not None
```

- [x] **Step 3: Run the tests to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_dashboard_api.py -p no:cacheprovider
```

Expected: FAIL with 404s — the route does not exist.

- [x] **Step 4: Write the schemas**

Create `backend/app/schemas/dashboard.py`:

```python
"""Response shapes for the dashboard.

Every group is independently nullable. A source that fails empties its own card
and nothing else, and ``None`` is distinguishable from a zero count — "0 active
bans" and "CrowdSec is unreachable" mean opposite things.
"""

from __future__ import annotations

from pydantic import BaseModel


class CertificateHealth(BaseModel):
    expiring_soon: int
    expired: int
    failed: int
    total: int


class InventoryCounts(BaseModel):
    proxy_hosts_total: int
    proxy_hosts_enabled: int
    redirection_hosts: int
    dead_hosts: int
    streams: int


class TrafficSummary(BaseModel):
    active_connections: int | None
    requests_per_second: float | None
    reporting_nodes: int
    stale_nodes: int


class SecuritySummary(BaseModel):
    active_decisions: int
    alerts_24h: int
    top_scenarios: list[str]


class ConfigHealth(BaseModel):
    config_version: int
    nodes_total: int
    nodes_in_sync: int
    nodes_stale: int
    converged: bool


class DashboardSummary(BaseModel):
    certificates: CertificateHealth
    inventory: InventoryCounts
    traffic: TrafficSummary
    config: ConfigHealth
    # None when CrowdSec could not be reached.
    security: SecuritySummary | None
```

- [x] **Step 5: Write the summary service**

Create `backend/app/services/dashboard/summary.py`:

```python
"""Every dashboard number, gathered in one pass.

Only the CrowdSec call is allowed to fail softly. Every other source is the
local database, where a failure is a real error and must not be disguised as an
empty card.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.certificate import Certificate
from app.models.dead_host import DeadHost
from app.models.enums import CertificateStatus
from app.models.proxy_host import ProxyHost
from app.models.redirection_host import RedirectionHost
from app.models.stream import Stream
from app.schemas.dashboard import (
    CertificateHealth,
    ConfigHealth,
    DashboardSummary,
    InventoryCounts,
    SecuritySummary,
    TrafficSummary,
)
from app.services.cluster.status import compute_cluster_status
from app.services.dashboard.metrics import load_traffic

EXPIRY_WINDOW_DAYS = 30


async def _count(db: AsyncSession, model, *where) -> int:
    stmt = select(func.count()).select_from(model)
    for clause in where:
        stmt = stmt.where(clause)
    return int(await db.scalar(stmt) or 0)


async def _certificates(db: AsyncSession, now: datetime) -> CertificateHealth:
    cutoff = now + timedelta(days=EXPIRY_WINDOW_DAYS)
    return CertificateHealth(
        # Active and inside the window: an already-expired certificate is
        # counted once, in `expired`, not twice.
        expiring_soon=await _count(
            db,
            Certificate,
            Certificate.status == CertificateStatus.active,
            Certificate.expires_on.is_not(None),
            Certificate.expires_on <= cutoff,
            Certificate.expires_on > now,
        ),
        expired=await _count(db, Certificate, Certificate.status == CertificateStatus.expired),
        failed=await _count(db, Certificate, Certificate.status == CertificateStatus.failed),
        total=await _count(db, Certificate),
    )


async def _inventory(db: AsyncSession) -> InventoryCounts:
    return InventoryCounts(
        proxy_hosts_total=await _count(db, ProxyHost),
        proxy_hosts_enabled=await _count(db, ProxyHost, ProxyHost.enabled.is_(True)),
        redirection_hosts=await _count(db, RedirectionHost),
        dead_hosts=await _count(db, DeadHost),
        streams=await _count(db, Stream),
    )


async def _security(crowdsec_client) -> SecuritySummary | None:
    """None on any failure: the card must be able to say "unavailable" rather
    than report zero attacks, which means the opposite."""
    try:
        decisions = await crowdsec_client.list_decisions()
        alerts = await crowdsec_client.list_alerts()
    except Exception:  # noqa: BLE001 - any failure degrades this one card
        return None

    scenarios: dict[str, int] = {}
    for alert in alerts:
        if alert.scenario:
            scenarios[alert.scenario] = scenarios.get(alert.scenario, 0) + 1
    top = sorted(scenarios, key=lambda k: (-scenarios[k], k))[:5]
    return SecuritySummary(
        active_decisions=len(decisions), alerts_24h=len(alerts), top_scenarios=top
    )


async def build_summary(db: AsyncSession, *, crowdsec_client) -> DashboardSummary:
    now = datetime.now(UTC)
    cluster = await compute_cluster_status(db)
    totals = await load_traffic(
        db, now=now, stale_after=settings.node_liveness_window_seconds
    )
    return DashboardSummary(
        certificates=await _certificates(db, now),
        inventory=await _inventory(db),
        traffic=TrafficSummary(
            active_connections=totals.active_connections,
            requests_per_second=totals.requests_per_second,
            reporting_nodes=totals.reporting_nodes,
            stale_nodes=totals.stale_nodes,
        ),
        config=ConfigHealth(
            config_version=cluster.config_version,
            nodes_total=len(cluster.nodes),
            nodes_in_sync=sum(1 for n in cluster.nodes if n.in_sync and not n.stale),
            nodes_stale=sum(1 for n in cluster.nodes if n.stale),
            converged=cluster.converged,
        ),
        security=await _security(crowdsec_client),
    )
```

Note the two names for one idea: `TrafficTotals` is the service dataclass,
`TrafficSummary` the response model. They are kept separate so the service stays
usable without importing Pydantic, and `build_summary` is the only place that
maps between them.

Check `list_decisions`/`list_alerts` against their real signatures in
`app/api/routes/crowdsec.py` and pass a 24-hour window to the alert call.

- [x] **Step 6: Write the route**

Create `backend/app/api/routes/dashboard.py`:

```python
@router.get("/summary", response_model=DashboardSummary)
async def dashboard_summary(
    _admin: AdminUser, db: SessionDep, client: ClientDep
) -> DashboardSummary:
    """Every card's numbers in one payload. Admin-only."""
    return await build_summary(db, crowdsec_client=client)
```

Register the router alongside the others, and check how `crowdsec.py` obtains
`ClientDep` so the CrowdSec dependency matches.

- [x] **Step 7: Run the tests and refresh the contract**

```bash
docker exec megoopm-test python -m pytest -p no:cacheprovider
docker exec megoopm-test python -m scripts.export_openapi
docker exec megoopm-test python -m pytest -p no:cacheprovider
docker exec megoopm-test ruff check app tests alembic
```

Expected: all pass, ruff clean.

- [x] **Step 8: Commit**

```bash
git add backend/app backend/tests backend/openapi.json
git commit -m "feat(dashboard): one endpoint for every card's numbers"
```

---

### Task 5: The threats endpoint

**Files:**
- Create: `backend/app/services/dashboard/threats.py`
- Modify: `backend/app/schemas/dashboard.py`
- Modify: `backend/app/api/routes/dashboard.py`
- Test: `backend/tests/test_dashboard_threats.py` (create)

**Interfaces:**
- Produces: `GET /api/v1/dashboard/threats` returning `list[ThreatPoint]` where `ThreatPoint(country, count, lat, lng)`.

- [x] **Step 1: Write the failing tests**

Create `backend/tests/test_dashboard_threats.py`. The grouping is pure — feed it `Alert` objects directly:

```python
"""Grouping CrowdSec alerts into globe points.

Pure: no network. The globe's whole contract is this list, and it is
deliberately not CrowdSec-shaped so P2's traffic layer can produce the same
thing.
"""

from __future__ import annotations

from app.schemas.crowdsec import Alert, AlertSource
from app.services.dashboard.threats import group_by_country


def _alert(cn: str | None, ip: str = "1.2.3.4") -> Alert:
    return Alert(scenario="x", source=AlertSource(ip=ip, cn=cn))


def test_groups_alerts_by_country() -> None:
    points = group_by_country([_alert("DE"), _alert("DE"), _alert("FR")])
    counts = {p.country: p.count for p in points}
    assert counts == {"DE": 2, "FR": 1}


def test_alerts_with_no_country_are_dropped_not_grouped_as_unknown() -> None:
    """A bucket labelled 'unknown' cannot be placed on a globe, and a point at
    0,0 would put it in the Atlantic."""
    points = group_by_country([_alert(None), _alert("DE")])
    assert [p.country for p in points] == ["DE"]


def test_every_point_carries_coordinates() -> None:
    points = group_by_country([_alert("JP")])
    assert points[0].lat is not None and points[0].lng is not None


def test_an_unrecognised_country_code_is_dropped() -> None:
    points = group_by_country([_alert("ZZ"), _alert("DE")])
    assert [p.country for p in points] == ["DE"]


def test_points_are_ordered_by_count_descending() -> None:
    points = group_by_country([_alert("FR"), _alert("DE"), _alert("DE")])
    assert [p.country for p in points] == ["DE", "FR"]


def test_no_alerts_is_an_empty_list_not_an_error() -> None:
    assert group_by_country([]) == []
```

- [x] **Step 2: Run the tests to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_dashboard_threats.py -p no:cacheprovider
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.dashboard.threats'`.

- [x] **Step 3: Implement the grouping**

Create `backend/app/services/dashboard/threats.py`:

```python
"""Turn CrowdSec alerts into points a map can draw.

Pure: no network, no database. The output type is deliberately not
CrowdSec-shaped, so P2's request pipeline can produce the same list and the
globe component never learns where its data came from.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.schemas.crowdsec import Alert
from app.schemas.dashboard import ThreatPoint

# ISO-3166 alpha-2 -> approximate country centroid (lat, lng).
#
# Static data in the repo rather than a dependency: it is a table that has not
# meaningfully changed in decades, and a package would need vetting, updating
# and a licence review to supply it. Fill in the full ~250 entries; the sample
# below shows the shape.
_CENTROIDS: dict[str, tuple[float, float]] = {
    "AE": (23.42, 53.85),
    "AU": (-25.27, 133.78),
    "BR": (-14.24, -51.93),
    "CN": (35.86, 104.20),
    "DE": (51.17, 10.45),
    "EG": (26.82, 30.80),
    "FR": (46.23, 2.21),
    "GB": (55.38, -3.44),
    "IN": (20.59, 78.96),
    "JP": (36.20, 138.25),
    "NL": (52.13, 5.29),
    "RU": (61.52, 105.32),
    "SA": (23.89, 45.08),
    "US": (37.09, -95.71),
    # ... remaining ISO-3166 alpha-2 codes
}


def group_by_country(alerts: Iterable[Alert]) -> list[ThreatPoint]:
    """Count alerts per country, dropping any that cannot be placed.

    An alert with no country, or a code with no centroid, is dropped rather
    than bucketed as "unknown": an unknown bucket cannot be drawn, and a point
    at 0,0 would put it in the Atlantic and read as real.
    """
    counts: dict[str, int] = {}
    for alert in alerts:
        source = alert.source
        if source is None or not source.cn:
            continue
        code = source.cn.upper()
        if code not in _CENTROIDS:
            continue
        counts[code] = counts.get(code, 0) + 1

    # Count descending, then country ascending: a stable order so the map and
    # its list do not reshuffle between identical polls.
    ordered = sorted(counts, key=lambda c: (-counts[c], c))
    return [
        ThreatPoint(
            country=code,
            count=counts[code],
            lat=_CENTROIDS[code][0],
            lng=_CENTROIDS[code][1],
        )
        for code in ordered
    ]


__all__ = ["group_by_country"]
```

**On the centroid table:** it is static data, so it belongs in the repo rather
than a dependency — a dict of ~250 entries. Do not reach for a package for this;
it is a table, and a dependency would need vetting, updating and a licence
review for data that has not changed in decades. Cite the source in a comment.

- [x] **Step 4: Add the schema and the route**

Add to `backend/app/schemas/dashboard.py`:

```python
class ThreatPoint(BaseModel):
    """One country's attack count, ready to place on a map.

    Deliberately not CrowdSec-shaped: P2's traffic layer will produce the same
    type from request logs, and the globe component must not need changing.
    """

    country: str
    count: int
    lat: float
    lng: float
```

and the route to `dashboard.py`:

```python
@router.get("/threats", response_model=list[ThreatPoint])
async def dashboard_threats(
    _admin: AdminUser, client: ClientDep
) -> list[ThreatPoint]:
    """Attack origins by country. Separate from the summary because it is the
    only part that needs CrowdSec, so an outage empties the globe rather than
    the page."""
    alerts = await client.list_alerts(...)
    return group_by_country(alerts)
```

Match `list_alerts`' real signature from `app/api/routes/crowdsec.py`, and pass a
24-hour window.

- [x] **Step 5: Run everything and refresh the contract**

```bash
docker exec megoopm-test python -m pytest -p no:cacheprovider
docker exec megoopm-test python -m scripts.export_openapi
docker exec megoopm-test python -m pytest -p no:cacheprovider
docker exec megoopm-test ruff check app tests alembic
```

- [x] **Step 6: Commit**

```bash
git add backend/app backend/tests backend/openapi.json
git commit -m "feat(dashboard): group attack origins into map points"
```

---

### Task 6: The page and its cards

**Files:**
- Modify: `frontend/src/app/(app)/page.tsx`
- Modify: `frontend/src/config/nav.ts`
- Modify: `frontend/src/components/app-sidebar.tsx`
- Create: `frontend/src/components/dashboard/dashboard-view.tsx` and one file per card
- Create: `frontend/src/lib/api/resources/dashboard.ts`
- Test: one test file per card, plus `dashboard-view.test.tsx`

**Interfaces:**
- Consumes: `GET /dashboard/summary`, `GET /dashboard/threats`.
- Produces: `dashboard.summary()` and `dashboard.threats()` on the API client.

- [x] **Step 1: Regenerate the API types**

```bash
cd frontend && npm run gen:api
```

Then add `src/lib/api/resources/dashboard.ts` following `resources/settings.ts`, and export it from `src/lib/api/index.ts`.

- [x] **Step 2: Make `/` the dashboard**

`src/app/(app)/page.tsx` currently redirects, with the comment *"The shell has
no dedicated dashboard yet; land on the first product area."* That is now
false. Replace it with the dashboard page:

```tsx
import type { Metadata } from "next";

import { DashboardView } from "@/components/dashboard/dashboard-view";

export const metadata: Metadata = { title: "Dashboard" };

export default function DashboardPage() {
  return <DashboardView />;
}
```

Set `HOME_ROUTE = "/"` in `src/config/nav.ts` and add the nav entry **first**,
before Proxy Hosts:

```ts
  {
    title: "Dashboard",
    href: "/",
    icon: LayoutDashboard,
    description: "Instance health, traffic and attack origins at a glance.",
  },
```

**Then fix `isActivePath`**, which will otherwise mark Dashboard active on every
page: `pathname.startsWith("/")` is true for every path. In
`src/components/app-sidebar.tsx`:

```ts
function isActivePath(pathname: string, href: string): boolean {
  // "/" is a prefix of everything, so it only ever matches exactly.
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}
```

Add a test for exactly that in the sidebar or nav test file:

```ts
it("marks Dashboard active only on the dashboard itself", () => {
  expect(isActivePath("/proxy-hosts", "/")).toBe(false);
  expect(isActivePath("/", "/")).toBe(true);
});
```

`nav.test.ts` asserts `primaryNav.some((item) => item.href === HOME_ROUTE)` —
that stays true with `HOME_ROUTE = "/"` and the new entry.

- [x] **Step 3: Write the failing card tests**

One file per card. Each renders from a fixture and asserts the number, the empty
state, and — for traffic and security — the *unmeasured* state, which must not
read as zero:

```tsx
it("says traffic is unmeasured rather than zero before any scrape", () => {
  render(<TrafficCard traffic={{ active_connections: null, requests_per_second: null, reporting_nodes: 0, stale_nodes: 0 }} />);
  expect(screen.getByText(/no data/i)).toBeInTheDocument();
  expect(screen.queryByText("0")).not.toBeInTheDocument();
});

it("says CrowdSec is unavailable rather than showing no threats", () => {
  render(<SecurityCard security={null} />);
  expect(screen.getByText(/unavailable/i)).toBeInTheDocument();
});

it("warns when a node is not reporting", () => {
  render(<TrafficCard traffic={{ active_connections: 4, requests_per_second: 1, reporting_nodes: 1, stale_nodes: 1 }} />);
  expect(screen.getByText(/1 node not reporting/i)).toBeInTheDocument();
});
```

- [x] **Step 4: Build the cards and the view**

`DashboardView` loads both endpoints, polls the summary every 15 seconds
(`settings.metrics_scrape_interval_seconds` is the same figure, so a faster poll
returns identical numbers), and renders the cards in a responsive grid in this
order, each taking exactly the matching group from the payload:

| component | prop | from |
| --- | --- | --- |
| `CertificatesCard` | `certificates: CertificateHealth` | `summary.certificates` |
| `ConfigHealthCard` | `config: ConfigHealth` | `summary.config` |
| `SecurityCard` | `security: SecuritySummary \| null` | `summary.security` |
| `TrafficCard` | `traffic: TrafficSummary` | `summary.traffic` |
| `InventoryCard` | `inventory: InventoryCounts` | `summary.inventory` |

Each card takes only its own group, so a card can be rendered from a fixture
with no knowledge of the rest of the payload — which is what makes the tests in
Step 3 possible.

Label the rate explicitly as an average — "req/s (15s avg)" — rather than
implying a live figure. The spec is explicit that presenting it as live would
misrepresent the data.

Follow the loading/error conventions of an existing view such as
`certificates-view.tsx`.

- [x] **Step 5: Run the full frontend gate**

```bash
cd frontend && npx vitest run && npm run typecheck && npm run lint && npm run build
```

Expected: all pass. `typecheck` is the one that catches fixtures broken by the
new response types.

- [x] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(dashboard): the dashboard page and its cards"
```

---

### Task 7: The globe

**Files:**
- Create: `frontend/src/components/dashboard/threat-globe.tsx` and its test
- Modify: `frontend/src/components/dashboard/dashboard-view.tsx`

**Interfaces:**
- Consumes: `ThreatPoint[]` from Task 5.
- Produces: `<ThreatGlobe points={points} />`.

- [x] **Step 1: Measure before choosing**

Build the page with a candidate library and record the bundle delta:

```bash
cd frontend && npm run build
```

Compare the route's reported First Load JS against the figure before the globe
was added. The spec's rule: if a 3D globe is disproportionate for the page,
fall back to a flat world map with a country choropleth, which answers the same
question at a fraction of the weight.

Record the measured numbers and the decision in the commit message. Do not skip
this step and pick from memory — bundle sizes change release to release, and the
whole point of deferring the choice was to decide it with a number.

- [x] **Step 2: Write the failing tests**

The component's contract is the props, not the rendering technology, so the
tests must pass for either choice:

```tsx
it("renders nothing but an empty state when no attacks were flagged", () => {
  render(<ThreatGlobe points={[]} />);
  expect(screen.getByText(/no attacks flagged/i)).toBeInTheDocument();
});

it("lists the top origins as text alongside the map", () => {
  // The map is not readable by a screen reader, so the same data must exist
  // as text. This also makes the component testable without a canvas.
  render(<ThreatGlobe points={[{ country: "DE", count: 9, lat: 51, lng: 10 }]} />);
  expect(screen.getByText(/DE/)).toBeInTheDocument();
  expect(screen.getByText("9")).toBeInTheDocument();
});
```

- [x] **Step 3: Build the component**

Render the map plus a short ranked list of origins. The list is not decoration:
a canvas or WebGL globe is invisible to assistive technology, so the text list
is what makes the data available at all — and it is what the tests assert on,
which keeps them independent of the rendering choice.

Guard against WebGL being unavailable (a headless browser, a locked-down
client): fall back to the list alone rather than a blank box.

- [x] **Step 4: Run the full frontend gate**

```bash
cd frontend && npx vitest run && npm run typecheck && npm run lint && npm run build
```

- [x] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(dashboard): show attack origins on a map"
```

---

## Manual verification

Not reachable by any automated test:

1. Bring the stack up. Confirm the dashboard loads at `/` and the sidebar marks
   Dashboard active there and nowhere else.
2. `docker compose exec nginx curl -s localhost:8081/stub_status` — confirm the
   body, and confirm the port is **not** reachable from the host.
3. Wait 15 seconds and confirm the traffic card leaves its "no data" state.
4. Generate traffic (`for i in $(seq 200); do curl -s localhost >/dev/null; done`)
   and confirm req/s rises on the next poll, then decays.
5. Stop nginx; confirm the traffic card degrades to "not reporting" rather than
   showing zero.
6. Stop CrowdSec; confirm the security card and globe show unavailable while
   certificates and inventory still render — this is the constraint that matters
   most on the page.
7. `cscli decisions add --ip <some-ip> --duration 5m`, then confirm the ban count
   rises and the globe places a point.


---

## Executed 2026-09-02

All seven tasks complete. Backend **760 passed, 41 skipped**, ruff clean.
Frontend **420 passed, 1 skipped**, typecheck, lint and build clean.

Four deviations from the plan, all deliberate:

- **No centroid table.** The plan had a static ~250-entry table of country
  centroids. CrowdSec's `geoip-enrich` parser already resolves coordinates for
  every alert — its own description is "Populate event with geoloc info : as,
  country, coords, source range" — so `AlertSource` gained `latitude` and
  `longitude` and each country's point is the mean position of the attackers
  actually seen. Less data to maintain, more accurate, and nothing to drift.
  `ThreatPoint.lat/lng` became nullable so a country with no coordinates is
  still counted and ranked, just not plotted.
- **The HA routing test no longer skips.** It was skipping whenever
  `ha_enabled` was false, which hid the plan's own constraint #1. It now calls
  `_configure_ha` directly and asserts the queue.
- **`cobe` chosen by measurement**, as required: +36 KB to the built assets
  (2487 → 2523 KB).
- **`plottable` had to be memoised.** A fresh array each render made the globe
  effect tear down and rebuild on every poll, flickering and leaking WebGL
  contexts. Caught by reading the dependency array, not by a test.

Two things the plan warned about happened exactly as written: `alembic upgrade`
polluted the shared test database (reset before running the suite), and
`typecheck` caught what `vitest` could not — `cobe` has no `onRender` hook in
this version, so rotation is driven by `update()` from an animation frame.
