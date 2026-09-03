# CrowdSec Hub Updates and Community Blocklist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A scheduled CrowdSec hub refresh with an Update now button, a switch for the community blocklist, and a Security → Updates tab that controls both and shows what happened.

**Architecture:** Five settings columns and a per-kind `crowdsec_job_run` status table. A docker-exec helper beside the existing restart helper. Two pure job flows (hub update, CAPI apply) that take `exec`, `restart` and `healthy` as callables — the whitelist task's shape — wrapped in Celery tasks with a Redis lock and an hourly beat tick that decides whether the slot is due. The blocklist switch works by making the app own `config.yaml.local`.

**Tech Stack:** Everything already in the repo: FastAPI, SQLAlchemy, Alembic, Celery, redis-py (sync client for the lock), httpx over the docker socket, Next.js + base-ui on the frontend.

**Spec:** `docs/superpowers/specs/2026-09-04-crowdsec-updates-design.md`

## Global Constraints

- **Measured against `crowdsecurity/crowdsec:v1.6.4`**: `cscli hub list -o json` is a dict keyed by item type, each item has `name`, `local_version`, `status`; `cscli hub update` warns `A new CrowdSec release is available (vX.Y.Z)`; `cscli hub upgrade` prints `updated <name>` lines and exits 0 either way; `cscli capi register` refuses to run until `api.server.online_client` exists in the merged config; `cscli capi status` exits 0 and prints `You can successfully interact with Central API (CAPI)` when working, and has no JSON output; an `online_client` block pointing at a missing credentials file **stops CrowdSec from starting**.
- **The container env keeps `DISABLE_ONLINE_API=true`.** CAPI is enabled solely through the app-owned `config.yaml.local`.
- **No restart unless something changed.** Every restart is a few seconds of fail-closed denial on protected hosts.
- **Rollback is best effort for the hub** (untar over, cannot remove added files) and **exact for CAPI** (previous file restored).
- **Hour of day is stored in UTC**; the UI picks in browser-local time.
- **Defaults:** auto-update on, daily, Sunday (6), 03:00 UTC, blocklist off.
- **Copy:** Update now confirm: *"This checks the CrowdSec hub for newer rules. If anything changed, CrowdSec restarts and protected hosts deny traffic for a few seconds."* Blocklist confirm: *"CrowdSec restarts and protected hosts deny traffic for a few seconds."* plus, when enabling, *"This registers this instance with CrowdSec's central service."* Running: 409 *"An update is already running."*
- **Backend tests run in the container recipe** (Task 1, Step 2). Frontend commands run from `frontend/`. Format frontend files with `npx prettier --write --print-width 100 <files>` only. Commit in a separate call after reading the test result.

## File Structure

**Backend**

| file | responsibility |
| --- | --- |
| `app/models/enums.py` | `HubUpdateFrequency`, `CrowdSecJobKind`, `CrowdSecJobTrigger` |
| `app/models/instance_settings.py` | five columns |
| `app/models/crowdsec_job_run.py` | the status table |
| `alembic/versions/0030_crowdsec_updates.py` | migration |
| `app/core/config.py` | `crowdsec_config_local_path` |
| `app/services/crowdsec/job_run.py` | sync read/start/finish of a run |
| `app/services/crowdsec/reload.py` | `exec_in_container` |
| `app/services/crowdsec/hub.py` | pure hub helpers + `run_hub_update` |
| `app/services/crowdsec/capi.py` | config render, status parse, `run_capi_apply` |
| `app/tasks/crowdsec.py` | `update_hub`, `hub_update_tick`, `apply_capi` |
| `app/core/celery_app.py` | beat entry, HA routes |
| `app/schemas/instance_settings.py`, `app/schemas/crowdsec.py` | shapes |
| `app/services/instance_settings.py` | two update functions |
| `app/api/routes/settings.py`, `app/api/routes/crowdsec.py` | routes |

**Frontend**

| file | responsibility |
| --- | --- |
| `src/lib/api/resources/settings.ts`, `crowdsec.ts` | calls and types |
| `src/components/security/hub-updates-card.tsx` | schedule, Update now, status |
| `src/components/security/blocklist-card.tsx` | the switch and its state |
| `src/components/security/updates-tab.tsx` | loads settings + status, polls, renders both cards |
| `src/components/security/updates-lib.ts` | local↔UTC hour, status wording |
| `src/components/security/security-view.tsx` | the tab |

**Infra**

| file | responsibility |
| --- | --- |
| `docker-compose.yml`, `docker-compose.dev.yml`, `docker-compose.ha.yml` | seeded `config.yaml.local` mount, `USE_WAL`; dev also gets the socket and data path |
| `docs/crowdsec.md` | the Updates tab, the CAPI row |

---

### Task 1: Storage and the run record

**Files:**
- Modify: `backend/app/models/enums.py`, `backend/app/models/instance_settings.py`, `backend/app/models/__init__.py`, `backend/app/core/config.py`, `backend/tests/conftest.py`
- Create: `backend/app/models/crowdsec_job_run.py`, `backend/alembic/versions/0030_crowdsec_updates.py`, `backend/app/services/crowdsec/job_run.py`
- Test: `backend/tests/test_crowdsec_job_run.py`

**Interfaces:**
- Produces:
  - enums `HubUpdateFrequency(daily, weekly)`, `CrowdSecJobKind(hub_update, capi_apply)`, `CrowdSecJobTrigger(scheduled, manual)`
  - `InstanceSettings.crowdsec_hub_auto_update: bool`, `.crowdsec_hub_update_frequency: HubUpdateFrequency`, `.crowdsec_hub_update_weekday: int`, `.crowdsec_hub_update_hour_utc: int`, `.crowdsec_capi_enabled: bool`
  - `CrowdSecJobRun(kind PK, started_at, finished_at, ok, error, trigger, restarted, detail: dict)`
  - `settings.crowdsec_config_local_path: str = "/data/crowdsec/config.yaml.local"`
  - `job_run.JobRun` dataclass; `read_job_run(conn, kind) -> JobRun | None`; `start_job_run(conn, kind, *, trigger, started_at=None) -> None`; `finish_job_run(conn, kind, *, ok, error, restarted, detail, finished_at=None) -> None` — all take a sync `Connection`; the clock defaults to now.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_crowdsec_job_run.py`:

```python
"""The per-kind run record, against an in-memory SQLite engine."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from app.models.crowdsec_job_run import CrowdSecJobRun
from app.models.enums import CrowdSecJobKind, CrowdSecJobTrigger
from app.services.crowdsec.job_run import finish_job_run, read_job_run, start_job_run
from sqlalchemy import Connection, create_engine


@pytest.fixture
def conn() -> Iterator[Connection]:
    engine = create_engine("sqlite://")
    CrowdSecJobRun.__table__.create(engine)
    with engine.begin() as c:
        yield c
    engine.dispose()


def test_missing_row_reads_as_none(conn: Connection) -> None:
    assert read_job_run(conn, CrowdSecJobKind.hub_update) is None


def test_start_then_finish_round_trips(conn: Connection) -> None:
    start_job_run(conn, CrowdSecJobKind.hub_update, trigger=CrowdSecJobTrigger.manual)
    running = read_job_run(conn, CrowdSecJobKind.hub_update)
    assert running is not None
    assert running.finished_at is None and running.ok is False
    assert running.trigger is CrowdSecJobTrigger.manual

    finish_job_run(
        conn,
        CrowdSecJobKind.hub_update,
        ok=True,
        error=None,
        restarted=True,
        detail={"updated": ["collections:crowdsecurity/nginx"], "agent_version": "v1.6.4"},
    )
    done = read_job_run(conn, CrowdSecJobKind.hub_update)
    assert done is not None
    assert done.finished_at is not None and done.ok is True and done.restarted is True
    assert done.detail["updated"] == ["collections:crowdsecurity/nginx"]


def test_a_second_start_replaces_the_row(conn: Connection) -> None:
    # One row per kind: a new run wipes the previous outcome so the UI never
    # shows last week's error next to this run's "running".
    start_job_run(conn, CrowdSecJobKind.capi_apply, trigger=CrowdSecJobTrigger.manual)
    finish_job_run(conn, CrowdSecJobKind.capi_apply, ok=False, error="boom", restarted=False, detail={})
    start_job_run(conn, CrowdSecJobKind.capi_apply, trigger=CrowdSecJobTrigger.scheduled)
    row = read_job_run(conn, CrowdSecJobKind.capi_apply)
    assert row is not None and row.error is None and row.finished_at is None


def test_kinds_are_independent(conn: Connection) -> None:
    start_job_run(conn, CrowdSecJobKind.hub_update, trigger=CrowdSecJobTrigger.manual)
    assert read_job_run(conn, CrowdSecJobKind.capi_apply) is None
```

- [ ] **Step 2: Start the test stack and run them to verify they fail**

```bash
export MSYS_NO_PATHCONV=1
docker network create megoopm-testnet 2>/dev/null || true
docker run -d --name megoopm-testdb --network megoopm-testnet \
  -e POSTGRES_USER=megoopm -e POSTGRES_PASSWORD=megoopm -e POSTGRES_DB=megoopm postgres:16-alpine
docker run -d --name megoopm-test --user root --network megoopm-testnet \
  -v "C:/Projects/megoopm/backend:/src" -w /src \
  -e CELERY_TASK_ALWAYS_EAGER=true -e CELERY_RESULT_BACKEND=cache+memory:// \
  -e DATABASE_URL="postgresql+asyncpg://megoopm:megoopm@megoopm-testdb:5432/megoopm" \
  --entrypoint sleep megoopm-backend infinity
docker exec megoopm-test pip install -q "pytest>=8.2" "pytest-asyncio>=0.23" \
  "aiosqlite>=0.20" "ruff>=0.6" "maxminddb" "webauthn>=3.0" "cbor2>=5.6"
docker exec megoopm-test python -m pytest tests/test_crowdsec_job_run.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — `ModuleNotFoundError: app.models.crowdsec_job_run`.

- [ ] **Step 3: Enums, columns, config**

In `backend/app/models/enums.py`, after `AuthTokenKind`:

```python
class HubUpdateFrequency(enum.StrEnum):
    """How often the CrowdSec hub is refreshed."""

    daily = "daily"
    weekly = "weekly"


class CrowdSecJobKind(enum.StrEnum):
    """The maintenance jobs that record an outcome in ``crowdsec_job_run``."""

    hub_update = "hub_update"
    capi_apply = "capi_apply"


class CrowdSecJobTrigger(enum.StrEnum):
    scheduled = "scheduled"
    manual = "manual"
```

In `backend/app/models/instance_settings.py`, after `app_url` (import
`HubUpdateFrequency` from enums; `Boolean`, `Enum`, `Integer` are already
imported there — check the import line):

```python
    # --- CrowdSec maintenance (Security → Updates) ---------------------------
    # The hub refresh schedule. Hour is UTC; the UI converts. Defaults give a
    # fresh install current rules at a quiet hour without visiting the tab.
    crowdsec_hub_auto_update: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    crowdsec_hub_update_frequency: Mapped[HubUpdateFrequency] = mapped_column(
        Enum(
            HubUpdateFrequency,
            name="hub_update_frequency",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=HubUpdateFrequency.daily,
        server_default="daily",
    )
    # Monday = 0. Only consulted when the frequency is weekly.
    crowdsec_hub_update_weekday: Mapped[int] = mapped_column(
        Integer, nullable=False, default=6, server_default="6"
    )
    crowdsec_hub_update_hour_utc: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    # Desired state of the community blocklist. What was *achieved* lives in
    # crowdsec_job_run(kind=capi_apply); the UI shows both when they differ.
    crowdsec_capi_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
```

In `backend/app/core/config.py`, after `crowdsec_whitelist_path`:

```python
    # The app-owned CrowdSec config override (merged over config.yaml at load).
    # Seeded by data-init from infra/crowdsec/config.yaml.local and bind-mounted
    # into the container; the community-blocklist switch rewrites it.
    crowdsec_config_local_path: str = "/data/crowdsec/config.yaml.local"
```

- [ ] **Step 4: The model, the migration, the record helpers**

Create `backend/app/models/crowdsec_job_run.py`:

```python
"""One row per maintenance job kind: the last run and how it went.

The jobs run in Celery on the control-plane node and can fail long after the
API answered 202; this row is what the Updates tab reads. ``finished_at`` is
null while a run is in progress.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import CrowdSecJobKind, CrowdSecJobTrigger


class CrowdSecJobRun(Base):
    __tablename__ = "crowdsec_job_run"

    kind: Mapped[CrowdSecJobKind] = mapped_column(
        Enum(CrowdSecJobKind, name="crowdsec_job_kind", values_callable=lambda e: [m.value for m in e]),
        primary_key=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger: Mapped[CrowdSecJobTrigger] = mapped_column(
        Enum(CrowdSecJobTrigger, name="crowdsec_job_trigger", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    restarted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # hub_update: {updated: [..], agent_version, latest_agent_version}
    # capi_apply: {enabled: bool}
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")


__all__ = ["CrowdSecJobRun"]
```

Register it in `backend/app/models/__init__.py` (import + `__all__`) and add
`CrowdSecJobRun.__table__` to the SQLite `tables=[…]` list in
`backend/tests/conftest.py` with its import.

Create `backend/alembic/versions/0030_crowdsec_updates.py`:

```python
"""CrowdSec maintenance: hub schedule + blocklist columns; crowdsec_job_run

Revision ID: 0030_crowdsec_updates
Revises: 0029_passkey
Create Date: 2026-09-04 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0030_crowdsec_updates"
down_revision: str | None = "0029_passkey"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# op.add_column does NOT emit CREATE TYPE for an enum — only create_table does.
_FREQUENCY = sa.Enum("daily", "weekly", name="hub_update_frequency")
_KIND = sa.Enum("hub_update", "capi_apply", name="crowdsec_job_kind")
_TRIGGER = sa.Enum("scheduled", "manual", name="crowdsec_job_trigger")


def upgrade() -> None:
    bind = op.get_bind()
    _FREQUENCY.create(bind, checkfirst=True)
    op.add_column(
        "instance_settings",
        sa.Column("crowdsec_hub_auto_update", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "instance_settings",
        sa.Column("crowdsec_hub_update_frequency", _FREQUENCY, nullable=False, server_default="daily"),
    )
    op.add_column(
        "instance_settings",
        sa.Column("crowdsec_hub_update_weekday", sa.Integer(), nullable=False, server_default="6"),
    )
    op.add_column(
        "instance_settings",
        sa.Column("crowdsec_hub_update_hour_utc", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column(
        "instance_settings",
        sa.Column("crowdsec_capi_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_table(
        "crowdsec_job_run",
        sa.Column("kind", _KIND, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("trigger", _TRIGGER, nullable=False),
        sa.Column("restarted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("detail", sa.JSON(), nullable=False, server_default="{}"),
        sa.PrimaryKeyConstraint("kind", name=op.f("pk_crowdsec_job_run")),
    )


def downgrade() -> None:
    op.drop_table("crowdsec_job_run")
    _TRIGGER.drop(op.get_bind(), checkfirst=True)
    _KIND.drop(op.get_bind(), checkfirst=True)
    for column in (
        "crowdsec_capi_enabled",
        "crowdsec_hub_update_hour_utc",
        "crowdsec_hub_update_weekday",
        "crowdsec_hub_update_frequency",
        "crowdsec_hub_auto_update",
    ):
        op.drop_column("instance_settings", column)
    _FREQUENCY.drop(op.get_bind(), checkfirst=True)
```

Create `backend/app/services/crowdsec/job_run.py`:

```python
"""Read and record a maintenance job's last run.

Synchronous, like ``apply_state`` — Celery tasks are sync and drive
``app.services.cluster.sync_engine``. One row per kind: starting a run
replaces whatever the previous run left, so "running" never sits next to a
stale error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Connection, select

from app.models.crowdsec_job_run import CrowdSecJobRun
from app.models.enums import CrowdSecJobKind, CrowdSecJobTrigger


@dataclass(frozen=True, slots=True)
class JobRun:
    kind: CrowdSecJobKind
    started_at: datetime
    finished_at: datetime | None
    ok: bool
    error: str | None
    trigger: CrowdSecJobTrigger
    restarted: bool
    detail: dict


def read_job_run(conn: Connection, kind: CrowdSecJobKind) -> JobRun | None:
    table = CrowdSecJobRun.__table__
    row = conn.execute(select(table).where(table.c.kind == kind.value)).one_or_none()
    if row is None:
        return None
    return JobRun(
        kind=CrowdSecJobKind(row.kind),
        started_at=row.started_at,
        finished_at=row.finished_at,
        ok=row.ok,
        error=row.error,
        trigger=CrowdSecJobTrigger(row.trigger),
        restarted=row.restarted,
        detail=dict(row.detail or {}),
    )


def start_job_run(
    conn: Connection,
    kind: CrowdSecJobKind,
    *,
    trigger: CrowdSecJobTrigger,
    started_at: datetime | None = None,
) -> None:
    """Mark a run as in progress, wiping the previous outcome."""
    table = CrowdSecJobRun.__table__
    conn.execute(table.delete().where(table.c.kind == kind.value))
    conn.execute(
        table.insert().values(
            kind=kind.value,
            started_at=started_at or datetime.now(UTC),
            finished_at=None,
            ok=False,
            error=None,
            trigger=trigger.value,
            restarted=False,
            detail={},
        )
    )


def finish_job_run(
    conn: Connection,
    kind: CrowdSecJobKind,
    *,
    ok: bool,
    error: str | None,
    restarted: bool,
    detail: dict,
    finished_at: datetime | None = None,
) -> None:
    table = CrowdSecJobRun.__table__
    conn.execute(
        table.update()
        .where(table.c.kind == kind.value)
        .values(
            finished_at=finished_at or datetime.now(UTC),
            ok=ok,
            error=error,
            restarted=restarted,
            detail=detail,
        )
    )


__all__ = ["JobRun", "finish_job_run", "read_job_run", "start_job_run"]
```

- [ ] **Step 5: Run the tests to verify they pass, lint, commit**

```bash
docker exec megoopm-test ruff format app/models/enums.py app/models/instance_settings.py app/models/crowdsec_job_run.py alembic/versions/0030_crowdsec_updates.py app/services/crowdsec/job_run.py tests/test_crowdsec_job_run.py
docker exec megoopm-test python -m pytest tests/test_crowdsec_job_run.py tests/test_settings_api.py tests/test_auth.py -p no:cacheprovider -p no:warnings
docker exec megoopm-test ruff check app tests
```
Then, in a separate call after reading the result:
```bash
git add backend/app/models backend/app/core/config.py backend/tests/conftest.py backend/alembic/versions/0030_crowdsec_updates.py backend/app/services/crowdsec/job_run.py backend/tests/test_crowdsec_job_run.py
git commit -m "feat(crowdsec): maintenance settings and the per-kind run record

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Running commands in the container, and the pure hub helpers

**Files:**
- Modify: `backend/app/services/crowdsec/reload.py`
- Create: `backend/app/services/crowdsec/hub.py`
- Test: `backend/tests/test_crowdsec_exec.py`, `backend/tests/test_crowdsec_hub.py`

**Interfaces:**
- Produces (reload.py): `@dataclass ExecResult(exit_code: int, output: str)`; `exec_in_container(name, argv: list[str], *, socket_path, timeout_seconds, transport=None) -> ExecResult` raising `CrowdSecReloadError`.
- Produces (hub.py): `HUB_ITEM_TYPES`, `HUB_BACKUP_PATH`, command constants `CMD_LIST`, `CMD_UPDATE`, `CMD_UPGRADE`, `CMD_VERSION`, `CMD_BACKUP`, `CMD_RESTORE`; `parse_hub_list(text) -> dict[str, str]` keyed `"<type>:<name>"`; `diff_versions(before, after) -> list[str]`; `parse_agent_warning(text) -> str | None`; `parse_agent_version(text) -> str | None`; `output_tail(text, lines=20) -> str`; `is_due(*, now, auto_update, frequency, weekday, hour_utc, last_started_at) -> tuple[bool, str]`; `@dataclass HubUpdateResult(ok, error, restarted, updated, agent_version, latest_agent_version)` with `as_dict()`; `run_hub_update(*, exec, restart, healthy) -> HubUpdateResult` where `exec: Callable[[list[str]], ExecResult]`, `restart: Callable[[], None]`, `healthy: Callable[[], bool]`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_crowdsec_exec.py`:

```python
"""docker exec over the socket, against a mock transport."""

from __future__ import annotations

import json

import httpx
import pytest
from app.services.crowdsec.reload import CrowdSecReloadError, exec_in_container


def _daemon(*, exit_code: int = 0, output: str = "hello\n") -> tuple[httpx.MockTransport, list]:
    seen: list[tuple[str, str, dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        if request.url.path.endswith("/containers/megoopm-crowdsec-1/exec"):
            return httpx.Response(201, json={"Id": "exec123"})
        if request.url.path.endswith("/exec/exec123/start"):
            return httpx.Response(200, content=output.encode())
        if request.url.path.endswith("/exec/exec123/json"):
            return httpx.Response(200, json={"ExitCode": exit_code, "Running": False})
        return httpx.Response(404, text="no such thing")

    return httpx.MockTransport(handler), seen


def test_create_start_inspect_and_the_output_comes_back() -> None:
    transport, seen = _daemon(output="ok\n")
    result = exec_in_container(
        "megoopm-crowdsec-1", ["cscli", "hub", "update"],
        socket_path="/var/run/docker.sock", timeout_seconds=30, transport=transport,
    )
    assert (result.exit_code, result.output) == (0, "ok\n")
    methods_paths = [(m, p.split("/v1.43")[-1]) for m, p, _ in seen]
    assert methods_paths == [
        ("POST", "/containers/megoopm-crowdsec-1/exec"),
        ("POST", "/exec/exec123/start"),
        ("GET", "/exec/exec123/json"),
    ]
    create_body = seen[0][2]
    # A TTY so the stream is plain text, not 8-byte multiplexed frames.
    assert create_body == {
        "AttachStdout": True, "AttachStderr": True, "Tty": True, "Cmd": ["cscli", "hub", "update"],
    }


def test_a_non_zero_exit_is_returned_not_raised() -> None:
    transport, _ = _daemon(exit_code=1, output="level=fatal msg=boom\n")
    result = exec_in_container(
        "megoopm-crowdsec-1", ["cscli", "x"],
        socket_path="/var/run/docker.sock", timeout_seconds=30, transport=transport,
    )
    assert result.exit_code == 1 and "boom" in result.output


def test_missing_container_names_what_it_tried() -> None:
    transport = httpx.MockTransport(lambda _r: httpx.Response(404, text="No such container"))
    with pytest.raises(CrowdSecReloadError) as exc:
        exec_in_container(
            "nope", ["true"], socket_path="/var/run/docker.sock", timeout_seconds=30, transport=transport
        )
    assert "'nope'" in str(exc.value) and "/var/run/docker.sock" in str(exc.value)


def test_socket_error_names_the_socket_path() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no such file")

    with pytest.raises(CrowdSecReloadError) as exc:
        exec_in_container(
            "megoopm-crowdsec-1", ["true"],
            socket_path="/var/run/docker.sock", timeout_seconds=30, transport=httpx.MockTransport(handler),
        )
    assert "/var/run/docker.sock" in str(exc.value)
```

Create `backend/tests/test_crowdsec_hub.py`:

```python
"""The pure parts of the hub refresh, and the flow with fakes."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from app.models.enums import HubUpdateFrequency
from app.services.crowdsec import hub
from app.services.crowdsec.reload import CrowdSecReloadError, ExecResult

LIST_BEFORE = json.dumps(
    {
        "collections": [{"name": "crowdsecurity/nginx", "local_version": "0.2", "status": "enabled"}],
        "parsers": [{"name": "crowdsecurity/nginx-logs", "local_version": "0.5", "status": "enabled"}],
        "scenarios": [],
        "appsec-rules": [],
    }
)
LIST_AFTER = json.dumps(
    {
        "collections": [{"name": "crowdsecurity/nginx", "local_version": "0.3", "status": "enabled"}],
        "parsers": [
            {"name": "crowdsecurity/nginx-logs", "local_version": "0.5", "status": "enabled"},
            {"name": "crowdsecurity/new-thing", "local_version": "0.1", "status": "enabled"},
        ],
        "scenarios": [],
        "appsec-rules": [],
    }
)


# --- parsing ------------------------------------------------------------------


def test_parse_hub_list_keys_by_type_and_name() -> None:
    assert hub.parse_hub_list(LIST_BEFORE) == {
        "collections:crowdsecurity/nginx": "0.2",
        "parsers:crowdsecurity/nginx-logs": "0.5",
    }


def test_parse_hub_list_tolerates_garbage() -> None:
    assert hub.parse_hub_list("level=fatal not json") == {}


def test_diff_versions_reports_changed_and_new_never_removed() -> None:
    before, after = hub.parse_hub_list(LIST_BEFORE), hub.parse_hub_list(LIST_AFTER)
    assert hub.diff_versions(before, after) == [
        "collections:crowdsecurity/nginx",
        "parsers:crowdsecurity/new-thing",
    ]
    assert hub.diff_versions(after, before) == ["collections:crowdsecurity/nginx"]


def test_parse_agent_warning() -> None:
    text = "level=warning msg=\"A new CrowdSec release is available (v1.8.0). Your version is 'v1.6.4'.\""
    assert hub.parse_agent_warning(text) == "v1.8.0"
    assert hub.parse_agent_warning("level=info msg=\"Wrote index\"") is None


def test_parse_agent_version() -> None:
    assert hub.parse_agent_version("version: v1.6.4-523164f6\nCodename: alphaga\n") == "v1.6.4"
    assert hub.parse_agent_version("") is None


def test_output_tail_keeps_the_last_lines() -> None:
    text = "\n".join(f"line {i}" for i in range(50))
    tail = hub.output_tail(text, lines=3)
    assert tail == "line 47\nline 48\nline 49"


# --- is it due? -----------------------------------------------------------------


def _at(y: int, mo: int, d: int, h: int) -> datetime:
    return datetime(y, mo, d, h, 5, tzinfo=UTC)


def test_daily_is_due_at_the_hour_and_not_otherwise() -> None:
    kw = dict(auto_update=True, frequency=HubUpdateFrequency.daily, weekday=6, hour_utc=3, last_started_at=None)
    assert hub.is_due(now=_at(2026, 9, 4, 3), **kw)[0] is True
    assert hub.is_due(now=_at(2026, 9, 4, 4), **kw)[0] is False


def test_weekly_needs_the_weekday_too() -> None:
    # 2026-09-06 is a Sunday (weekday 6); 2026-09-04 is a Friday.
    kw = dict(auto_update=True, frequency=HubUpdateFrequency.weekly, weekday=6, hour_utc=3, last_started_at=None)
    assert hub.is_due(now=_at(2026, 9, 6, 3), **kw)[0] is True
    assert hub.is_due(now=_at(2026, 9, 4, 3), **kw)[0] is False


def test_not_due_when_off_or_already_ran_this_hour() -> None:
    off = hub.is_due(
        now=_at(2026, 9, 4, 3), auto_update=False, frequency=HubUpdateFrequency.daily,
        weekday=6, hour_utc=3, last_started_at=None,
    )
    assert off == (False, "auto-update is off")
    ran = hub.is_due(
        now=_at(2026, 9, 4, 3), auto_update=True, frequency=HubUpdateFrequency.daily,
        weekday=6, hour_utc=3, last_started_at=datetime(2026, 9, 4, 3, 1, tzinfo=UTC),
    )
    assert ran == (False, "already ran this hour")
    yesterday = hub.is_due(
        now=_at(2026, 9, 4, 3), auto_update=True, frequency=HubUpdateFrequency.daily,
        weekday=6, hour_utc=3, last_started_at=datetime(2026, 9, 3, 3, 1, tzinfo=UTC),
    )
    assert yesterday[0] is True


# --- the flow, with fakes ---------------------------------------------------------


class FakeContainer:
    """Answers each command from a script; records what ran."""

    def __init__(self, *, lists: list[str], upgrade: ExecResult | None = None, update: ExecResult | None = None):
        self.lists = list(lists)
        self.upgrade = upgrade or ExecResult(0, "updated crowdsecurity/nginx\n")
        self.update = update or ExecResult(0, "level=info msg=\"Wrote index\"\n")
        self.ran: list[list[str]] = []
        self.restarts = 0

    def exec(self, argv: list[str]) -> ExecResult:
        self.ran.append(argv)
        if argv == hub.CMD_LIST:
            return ExecResult(0, self.lists.pop(0))
        if argv == hub.CMD_VERSION:
            return ExecResult(0, "version: v1.6.4-abc\n")
        if argv == hub.CMD_UPDATE:
            return self.update
        if argv == hub.CMD_UPGRADE:
            return self.upgrade
        if argv in (hub.CMD_BACKUP, hub.CMD_RESTORE):
            return ExecResult(0, "")
        raise AssertionError(f"unexpected command {argv}")

    def restart(self) -> None:
        self.restarts += 1


def test_nothing_changed_means_no_restart() -> None:
    c = FakeContainer(lists=[LIST_BEFORE, LIST_BEFORE], upgrade=ExecResult(0, ""))
    result = hub.run_hub_update(exec=c.exec, restart=c.restart, healthy=lambda: True)
    assert result.ok and not result.restarted and result.updated == []
    assert c.restarts == 0
    assert hub.CMD_BACKUP in c.ran and hub.CMD_UPDATE in c.ran and hub.CMD_UPGRADE in c.ran
    assert result.agent_version == "v1.6.4"


def test_a_change_restarts_and_records_it() -> None:
    c = FakeContainer(
        lists=[LIST_BEFORE, LIST_AFTER],
        update=ExecResult(0, "level=warning msg=\"A new CrowdSec release is available (v1.8.0). Your version is 'v1.6.4'.\"\n"),
    )
    result = hub.run_hub_update(exec=c.exec, restart=c.restart, healthy=lambda: True)
    assert result.ok and result.restarted
    assert result.updated == ["collections:crowdsecurity/nginx", "parsers:crowdsecurity/new-thing"]
    assert result.latest_agent_version == "v1.8.0"
    assert c.restarts == 1
    assert hub.CMD_RESTORE not in c.ran


def test_unhealthy_after_restart_restores_the_backup_and_restarts_again() -> None:
    c = FakeContainer(lists=[LIST_BEFORE, LIST_AFTER])
    result = hub.run_hub_update(exec=c.exec, restart=c.restart, healthy=lambda: False)
    assert not result.ok and result.restarted
    assert "did not come back" in (result.error or "")
    assert c.ran[-1] == hub.CMD_RESTORE or c.ran[-2] == hub.CMD_RESTORE
    assert c.restarts == 2


def test_a_failed_upgrade_stops_before_any_restart() -> None:
    c = FakeContainer(lists=[LIST_BEFORE], upgrade=ExecResult(1, "a\nb\nlevel=fatal msg=\"network down\"\n"))
    result = hub.run_hub_update(exec=c.exec, restart=c.restart, healthy=lambda: True)
    assert not result.ok and not result.restarted
    assert "network down" in (result.error or "")
    assert c.restarts == 0


def test_a_failed_restart_is_reported() -> None:
    c = FakeContainer(lists=[LIST_BEFORE, LIST_AFTER])

    def boom() -> None:
        raise CrowdSecReloadError("docker says no")

    result = hub.run_hub_update(exec=c.exec, restart=boom, healthy=lambda: True)
    assert not result.ok and "docker says no" in (result.error or "")


def test_exec_failure_is_reported_not_raised() -> None:
    def broken(argv: list[str]) -> ExecResult:
        raise CrowdSecReloadError("Could not reach the docker daemon")

    result = hub.run_hub_update(exec=broken, restart=lambda: None, healthy=lambda: True)
    assert not result.ok and "docker daemon" in (result.error or "")
```

- [ ] **Step 2: Run them to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_crowdsec_exec.py tests/test_crowdsec_hub.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — `ImportError: cannot import name 'exec_in_container'`, `No module named 'app.services.crowdsec.hub'`.

- [ ] **Step 3: `exec_in_container`**

Append to `backend/app/services/crowdsec/reload.py` (add `from dataclasses import dataclass` at the top):

```python
@dataclass(frozen=True, slots=True)
class ExecResult:
    """What a command in the container produced."""

    exit_code: int
    output: str


def exec_in_container(
    name: str,
    argv: list[str],
    *,
    socket_path: str,
    timeout_seconds: float,
    transport: httpx.BaseTransport | None = None,
) -> ExecResult:
    """Run ``argv`` inside ``name`` and return its exit code and output.

    Three calls: create the exec, start it (with a TTY, so the stream is
    plain text rather than docker's multiplexed frames), inspect it for the
    exit code. A non-zero exit is a result, not an error: the caller reads
    the output. Errors are for the daemon being unreachable or refusing.
    """
    client_transport = transport or httpx.HTTPTransport(uds=socket_path)
    where = f"container {name!r} via {socket_path}"
    try:
        with httpx.Client(
            transport=client_transport, base_url="http://docker", timeout=timeout_seconds
        ) as client:
            created = client.post(
                f"/{_DOCKER_API}/containers/{name}/exec",
                json={"AttachStdout": True, "AttachStderr": True, "Tty": True, "Cmd": argv},
            )
            if created.status_code != httpx.codes.CREATED:
                raise CrowdSecReloadError(
                    f"Docker refused to exec in {where}: HTTP {created.status_code} — "
                    f"{created.text.strip() or 'no body'}"
                )
            exec_id = created.json()["Id"]
            started = client.post(
                f"/{_DOCKER_API}/exec/{exec_id}/start", json={"Detach": False, "Tty": True}
            )
            if started.status_code != httpx.codes.OK:
                raise CrowdSecReloadError(
                    f"Docker could not start the exec in {where}: HTTP {started.status_code}"
                )
            output = started.content.decode("utf-8", errors="replace")
            inspected = client.get(f"/{_DOCKER_API}/exec/{exec_id}/json")
            exit_code = int(inspected.json().get("ExitCode") or 0)
    except httpx.HTTPError as exc:
        detail = str(exc) or "no detail"
        raise CrowdSecReloadError(
            f"Could not reach the docker daemon to exec in {where}: "
            f"{type(exc).__name__} — {detail}"
        ) from exc
    return ExecResult(exit_code=exit_code, output=output)
```

Add `"ExecResult"`, `"exec_in_container"` to `__all__`.

- [ ] **Step 4: `hub.py`**

Create `backend/app/services/crowdsec/hub.py`:

```python
"""Refreshing CrowdSec's hub items: parsing, the due-check, and the flow.

Everything here is pure or takes its side effects as callables, so the
sequence — back up, update, upgrade, diff, restart only if something
changed, roll back if CrowdSec does not come back — is tested without a
docker socket. The Celery task in ``app.tasks.crowdsec`` supplies the real
``exec``/``restart``/``healthy``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from app.models.enums import HubUpdateFrequency
from app.services.crowdsec.reload import CrowdSecReloadError, ExecResult

HUB_ITEM_TYPES = (
    "hub",
    "collections",
    "parsers",
    "scenarios",
    "postoverflows",
    "contexts",
    "appsec-configs",
    "appsec-rules",
)
#: In the data volume, so it survives a restart and never lands in /etc/crowdsec.
HUB_BACKUP_PATH = "/var/lib/crowdsec/data/megoopm-hub-backup.tgz"

CMD_LIST = ["cscli", "hub", "list", "-o", "json"]
CMD_UPDATE = ["cscli", "hub", "update"]
CMD_UPGRADE = ["cscli", "hub", "upgrade"]
CMD_VERSION = ["cscli", "version"]
# Item directories are symlinks into hub/, so this captures the installed
# state. `ls -d` drops directories that do not exist on this install.
CMD_BACKUP = [
    "sh",
    "-c",
    f"cd /etc/crowdsec && tar -czf {HUB_BACKUP_PATH} $(ls -d {' '.join(HUB_ITEM_TYPES)} 2>/dev/null)",
]
# Untar OVER: the whitelist file is a bind mount inside parsers/s02-enrich
# and cannot be removed, so nothing is deleted first.
CMD_RESTORE = ["tar", "-xzf", HUB_BACKUP_PATH, "-C", "/etc/crowdsec"]

_AGENT_WARNING = re.compile(r"new CrowdSec release is available \((v[\d.]+)\)")
_AGENT_VERSION = re.compile(r"^version:\s*(v[\d.]+)", re.MULTILINE)


# --- parsing ------------------------------------------------------------------


def parse_hub_list(text: str) -> dict[str, str]:
    """``{"<type>:<name>": local_version}`` for every installed item."""
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for item_type, items in data.items():
        for item in items or []:
            name = item.get("name") if isinstance(item, dict) else None
            if name:
                out[f"{item_type}:{name}"] = str(item.get("local_version") or "")
    return out


def diff_versions(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Items whose version changed or that are new. Removals are not changes."""
    return sorted(key for key, version in after.items() if before.get(key) != version)


def parse_agent_warning(text: str) -> str | None:
    match = _AGENT_WARNING.search(text)
    return match.group(1) if match else None


def parse_agent_version(text: str) -> str | None:
    match = _AGENT_VERSION.search(text)
    return match.group(1) if match else None


def output_tail(text: str, lines: int = 20) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


# --- the schedule ----------------------------------------------------------------


def is_due(
    *,
    now: datetime,
    auto_update: bool,
    frequency: HubUpdateFrequency,
    weekday: int,
    hour_utc: int,
    last_started_at: datetime | None,
) -> tuple[bool, str]:
    """Whether the hourly tick at ``now`` should run the job, and why not."""
    if not auto_update:
        return False, "auto-update is off"
    if now.hour != hour_utc:
        return False, "not the configured hour"
    if frequency is HubUpdateFrequency.weekly and now.weekday() != weekday:
        return False, "not the configured weekday"
    if last_started_at is not None:
        # SQLite (the test factory) hands back naive datetimes; treat as UTC.
        if last_started_at.tzinfo is None:
            last_started_at = last_started_at.replace(tzinfo=UTC)
        same_hour = last_started_at.replace(minute=0, second=0, microsecond=0) == now.replace(
            minute=0, second=0, microsecond=0
        )
        if same_hour:
            return False, "already ran this hour"
    return True, "due"


# --- the flow -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HubUpdateResult:
    ok: bool
    error: str | None
    restarted: bool
    updated: list[str]
    agent_version: str | None
    latest_agent_version: str | None

    def as_dict(self) -> dict:
        return asdict(self)


def run_hub_update(
    *,
    exec: Callable[[list[str]], ExecResult],
    restart: Callable[[], None],
    healthy: Callable[[], bool],
) -> HubUpdateResult:
    """Back up, update, upgrade, diff; restart only if something changed."""
    agent_version = None
    latest = None
    try:
        agent_version = parse_agent_version(exec(CMD_VERSION).output)
        before = parse_hub_list(exec(CMD_LIST).output)
        backup = exec(CMD_BACKUP)
        if backup.exit_code != 0:
            return HubUpdateResult(False, f"Backup failed: {output_tail(backup.output)}", False, [], agent_version, None)
        update = exec(CMD_UPDATE)
        latest = parse_agent_warning(update.output)
        if update.exit_code != 0:
            return HubUpdateResult(False, f"hub update failed: {output_tail(update.output)}", False, [], agent_version, latest)
        upgrade = exec(CMD_UPGRADE)
        if upgrade.exit_code != 0:
            return HubUpdateResult(False, f"hub upgrade failed: {output_tail(upgrade.output)}", False, [], agent_version, latest)
        after = parse_hub_list(exec(CMD_LIST).output)
    except CrowdSecReloadError as exc:
        return HubUpdateResult(False, str(exc), False, [], agent_version, latest)

    updated = diff_versions(before, after)
    if not updated:
        return HubUpdateResult(True, None, False, [], agent_version, latest)

    try:
        restart()
    except CrowdSecReloadError as exc:
        return HubUpdateResult(False, str(exc), False, updated, agent_version, latest)
    if healthy():
        return HubUpdateResult(True, None, True, updated, agent_version, latest)

    # CrowdSec did not answer again: an upgraded item it cannot load. Put the
    # previous item files back and restart onto those.
    try:
        exec(CMD_RESTORE)
        restart()
    except CrowdSecReloadError as exc:
        return HubUpdateResult(
            False,
            f"CrowdSec did not come back after the hub upgrade, and the rollback also failed: {exc}",
            True,
            updated,
            agent_version,
            latest,
        )
    return HubUpdateResult(
        False,
        "CrowdSec did not come back after the hub upgrade. The previous rules were restored.",
        True,
        updated,
        agent_version,
        latest,
    )


__all__ = [
    "CMD_BACKUP",
    "CMD_LIST",
    "CMD_RESTORE",
    "CMD_UPDATE",
    "CMD_UPGRADE",
    "CMD_VERSION",
    "HUB_BACKUP_PATH",
    "HUB_ITEM_TYPES",
    "HubUpdateResult",
    "diff_versions",
    "is_due",
    "output_tail",
    "parse_agent_version",
    "parse_agent_warning",
    "parse_hub_list",
    "run_hub_update",
]
```

- [ ] **Step 5: Run, lint, commit**

```bash
docker exec megoopm-test ruff format app/services/crowdsec/reload.py app/services/crowdsec/hub.py tests/test_crowdsec_exec.py tests/test_crowdsec_hub.py
docker exec megoopm-test python -m pytest tests/test_crowdsec_exec.py tests/test_crowdsec_hub.py tests/test_crowdsec_reload.py -p no:cacheprovider -p no:warnings
docker exec megoopm-test ruff check app tests
```
Then commit separately:
```bash
git add backend/app/services/crowdsec/reload.py backend/app/services/crowdsec/hub.py backend/tests/test_crowdsec_exec.py backend/tests/test_crowdsec_hub.py
git commit -m "feat(crowdsec): docker exec over the socket, and the hub refresh flow

Back up, update, upgrade, diff, and restart only when something changed.
The flow takes its side effects as callables, like the whitelist apply, so
the rollback path is tested without a daemon.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The community blocklist switch, as a pure flow

**Files:**
- Create: `backend/app/services/crowdsec/capi.py`
- Test: `backend/tests/test_crowdsec_capi.py`

**Interfaces:**
- Produces: `CREDENTIALS_PATH`, `CMD_HAS_CREDENTIALS`, `CMD_REGISTER`, `CMD_STATUS`; `render_config_local(*, capi_enabled: bool) -> str`; `parse_capi_status(result: ExecResult) -> bool`; `@dataclass CapiApplyResult(ok, error, restarted, enabled)` with `as_dict()`; `run_capi_apply(*, enabled: bool, path: Path, exec, restart, healthy) -> CapiApplyResult`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_crowdsec_capi.py`:

```python
"""The community-blocklist switch: render, parse, and the flow with fakes."""

from __future__ import annotations

from pathlib import Path

from app.services.crowdsec import capi
from app.services.crowdsec.reload import CrowdSecReloadError, ExecResult


# --- render -------------------------------------------------------------------


def test_off_keeps_the_auto_registration_block_only() -> None:
    text = capi.render_config_local(capi_enabled=False)
    assert "auto_registration:" in text
    assert "${CROWDSEC_REGISTRATION_TOKEN}" in text
    assert "online_client" not in text


def test_on_adds_the_online_client_block() -> None:
    text = capi.render_config_local(capi_enabled=True)
    assert "auto_registration:" in text
    assert f"credentials_path: {capi.CREDENTIALS_PATH}" in text
    assert "sharing: true" in text and "community: true" in text and "blocklists: true" in text


def test_render_is_deterministic() -> None:
    assert capi.render_config_local(capi_enabled=True) == capi.render_config_local(capi_enabled=True)


# --- status -----------------------------------------------------------------------


def test_parse_capi_status() -> None:
    ok = ExecResult(0, "Loaded credentials\nYou can successfully interact with Central API (CAPI)\n")
    assert capi.parse_capi_status(ok) is True
    assert capi.parse_capi_status(ExecResult(1, 'level=fatal msg="no configuration for Central API"')) is False
    assert capi.parse_capi_status(ExecResult(0, "something else")) is False


# --- the flow ---------------------------------------------------------------------


class FakeContainer:
    def __init__(self, *, has_credentials: bool, register_ok: bool = True, status_ok: bool = True):
        self.has_credentials = has_credentials
        self.register_ok = register_ok
        self.status_ok = status_ok
        self.ran: list[list[str]] = []
        self.restarts = 0

    def exec(self, argv: list[str]) -> ExecResult:
        self.ran.append(argv)
        if argv == capi.CMD_HAS_CREDENTIALS:
            return ExecResult(0 if self.has_credentials else 1, "")
        if argv == capi.CMD_REGISTER:
            if self.register_ok:
                self.has_credentials = True
                return ExecResult(0, "Central API credentials written")
            return ExecResult(1, 'level=fatal msg="dial tcp: no route to host"')
        if argv == capi.CMD_STATUS:
            return ExecResult(0, "You can successfully interact with Central API (CAPI)") if self.status_ok else ExecResult(1, "nope")
        raise AssertionError(argv)

    def restart(self) -> None:
        self.restarts += 1


def _seed(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml.local"
    path.write_text(capi.render_config_local(capi_enabled=False), encoding="utf-8")
    return path


def test_enabling_registers_writes_restarts_and_verifies(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    c = FakeContainer(has_credentials=False)
    result = capi.run_capi_apply(enabled=True, path=path, exec=c.exec, restart=c.restart, healthy=lambda: True)
    assert result.ok and result.restarted and result.enabled
    assert capi.CMD_REGISTER in c.ran and capi.CMD_STATUS in c.ran
    assert "online_client" in path.read_text(encoding="utf-8")
    assert c.restarts == 1
    # The override was on disk BEFORE register ran: cscli refuses otherwise.
    assert c.ran.index(capi.CMD_REGISTER) > 0


def test_enabling_with_credentials_present_does_not_register_again(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    c = FakeContainer(has_credentials=True)
    result = capi.run_capi_apply(enabled=True, path=path, exec=c.exec, restart=c.restart, healthy=lambda: True)
    assert result.ok and capi.CMD_REGISTER not in c.ran


def test_unchanged_content_does_nothing(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    c = FakeContainer(has_credentials=True)
    result = capi.run_capi_apply(enabled=False, path=path, exec=c.exec, restart=c.restart, healthy=lambda: True)
    assert result.ok and not result.restarted and c.restarts == 0 and c.ran == []


def test_register_failure_restores_the_file_without_a_restart(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    before = path.read_text(encoding="utf-8")
    c = FakeContainer(has_credentials=False, register_ok=False)
    result = capi.run_capi_apply(enabled=True, path=path, exec=c.exec, restart=c.restart, healthy=lambda: True)
    assert not result.ok and not result.restarted
    assert "no route to host" in (result.error or "")
    assert path.read_text(encoding="utf-8") == before
    assert c.restarts == 0


def test_unhealthy_after_enable_rolls_back(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    before = path.read_text(encoding="utf-8")
    c = FakeContainer(has_credentials=True)
    result = capi.run_capi_apply(enabled=True, path=path, exec=c.exec, restart=c.restart, healthy=lambda: False)
    assert not result.ok and result.restarted
    assert path.read_text(encoding="utf-8") == before
    assert c.restarts == 2


def test_status_failure_after_enable_rolls_back(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    c = FakeContainer(has_credentials=True, status_ok=False)
    result = capi.run_capi_apply(enabled=True, path=path, exec=c.exec, restart=c.restart, healthy=lambda: True)
    assert not result.ok and "capi status" in (result.error or "").lower()
    assert "online_client" not in path.read_text(encoding="utf-8")


def test_disabling_writes_and_restarts_without_status_check(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml.local"
    path.write_text(capi.render_config_local(capi_enabled=True), encoding="utf-8")
    c = FakeContainer(has_credentials=True)
    result = capi.run_capi_apply(enabled=False, path=path, exec=c.exec, restart=c.restart, healthy=lambda: True)
    assert result.ok and result.restarted and not result.enabled
    assert capi.CMD_STATUS not in c.ran
    assert "online_client" not in path.read_text(encoding="utf-8")


def test_exec_failure_is_reported(tmp_path: Path) -> None:
    path = _seed(tmp_path)

    def broken(argv: list[str]) -> ExecResult:
        raise CrowdSecReloadError("Could not reach the docker daemon")

    result = capi.run_capi_apply(enabled=True, path=path, exec=broken, restart=lambda: None, healthy=lambda: True)
    assert not result.ok and "docker daemon" in (result.error or "")
    assert "online_client" not in path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run them to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_crowdsec_capi.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — `No module named 'app.services.crowdsec.capi'`.

- [ ] **Step 3: `capi.py`**

Create `backend/app/services/crowdsec/capi.py`:

```python
"""The community-blocklist switch: what config.yaml.local says, and applying it.

CrowdSec merges ``config.yaml.local`` over ``config.yaml`` at load time —
after the image's entrypoint has deleted ``online_client`` because
``DISABLE_ONLINE_API=true``. Putting the block back here is how the blocklist
is enabled without touching the container's env. The file must be on disk
before ``cscli capi register`` runs (it refuses otherwise), and it must never
point at a missing credentials file (CrowdSec then fails to start).

The auto_registration block below is the same text as
``infra/crowdsec/config.yaml.local``, which data-init uses to seed the file.
Keep the two in step.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from app.services.crowdsec.reload import CrowdSecReloadError, ExecResult

CREDENTIALS_PATH = "/etc/crowdsec/online_api_credentials.yaml"

CMD_HAS_CREDENTIALS = ["sh", "-c", f"test -s {CREDENTIALS_PATH} && grep -q login {CREDENTIALS_PATH}"]
CMD_REGISTER = ["cscli", "capi", "register", "-f", CREDENTIALS_PATH]
CMD_STATUS = ["cscli", "capi", "status"]

_BASE = """\
# Managed by MegooPM — Security → Updates rewrites this file. Edit
# infra/crowdsec/config.yaml.local in the repo for the seed.
#
# Machine auto-registration: the backend self-registers its LAPI machine with
# `POST /v1/watchers` and sends CROWDSEC_REGISTRATION_TOKEN; when it matches
# the token below and the request comes from an allowed range, LAPI validates
# the machine immediately. The token must be >= 32 characters.
api:
  server:
    auto_registration:
      enabled: true
      token: ${CROWDSEC_REGISTRATION_TOKEN}
      allowed_ranges:
        - 127.0.0.1/32
        - 10.0.0.0/8
        - 172.16.0.0/12
        - 192.168.0.0/16
"""

_ONLINE_CLIENT = f"""\
    # Community blocklist: switched on from Security → Updates.
    online_client:
      credentials_path: {CREDENTIALS_PATH}
      sharing: true
      pull:
        community: true
        blocklists: true
"""


def render_config_local(*, capi_enabled: bool) -> str:
    return _BASE + (_ONLINE_CLIENT if capi_enabled else "")


def parse_capi_status(result: ExecResult) -> bool:
    return result.exit_code == 0 and "successfully interact" in result.output


@dataclass(frozen=True, slots=True)
class CapiApplyResult:
    ok: bool
    error: str | None
    restarted: bool
    enabled: bool

    def as_dict(self) -> dict:
        return asdict(self)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _write(path: Path, content: str) -> None:
    # In place, never replaced: the container's bind mount is pinned to the
    # inode it saw at start (see the whitelist writer for the same rule).
    with path.open("w", encoding="utf-8") as fh:
        fh.write(content)


def run_capi_apply(
    *,
    enabled: bool,
    path: Path,
    exec: Callable[[list[str]], ExecResult],
    restart: Callable[[], None],
    healthy: Callable[[], bool],
) -> CapiApplyResult:
    """Write, register if needed, restart, verify — and roll back if it fails."""
    previous = _read(path)
    content = render_config_local(capi_enabled=enabled)
    if content == previous:
        return CapiApplyResult(True, None, False, enabled)

    _write(path, content)

    if enabled:
        try:
            if exec(CMD_HAS_CREDENTIALS).exit_code != 0:
                registered = exec(CMD_REGISTER)
                if registered.exit_code != 0:
                    _write(path, previous)
                    tail = registered.output.strip().splitlines()[-1:] or ["no output"]
                    return CapiApplyResult(
                        False, f"Registering with CrowdSec's central API failed: {tail[0]}", False, False
                    )
        except CrowdSecReloadError as exc:
            _write(path, previous)
            return CapiApplyResult(False, str(exc), False, False)

    try:
        restart()
    except CrowdSecReloadError as exc:
        _write(path, previous)
        return CapiApplyResult(False, str(exc), False, not enabled)

    verified = healthy()
    if verified and enabled:
        try:
            verified = parse_capi_status(exec(CMD_STATUS))
            reason = "cscli capi status did not confirm the connection"
        except CrowdSecReloadError as exc:
            verified, reason = False, str(exc)
    else:
        reason = "CrowdSec did not come back after the change"
    if verified:
        return CapiApplyResult(True, None, True, enabled)

    _write(path, previous)
    try:
        restart()
    except CrowdSecReloadError as exc:
        return CapiApplyResult(False, f"{reason}, and the rollback restart also failed: {exc}", True, not enabled)
    return CapiApplyResult(False, f"{reason}. The previous configuration was restored.", True, not enabled)


__all__ = [
    "CMD_HAS_CREDENTIALS",
    "CMD_REGISTER",
    "CMD_STATUS",
    "CREDENTIALS_PATH",
    "CapiApplyResult",
    "parse_capi_status",
    "render_config_local",
    "run_capi_apply",
]
```

Also update `infra/crowdsec/config.yaml.local` so its text equals `_BASE`
(the header comment included), so a data-init seed and an app render of the
"off" state are byte-identical and the first apply is a no-op.

- [ ] **Step 4: Run, lint, commit**

```bash
docker exec megoopm-test ruff format app/services/crowdsec/capi.py tests/test_crowdsec_capi.py
docker exec megoopm-test python -m pytest tests/test_crowdsec_capi.py -p no:cacheprovider -p no:warnings
docker exec megoopm-test ruff check app tests
```
Commit separately:
```bash
git add backend/app/services/crowdsec/capi.py backend/tests/test_crowdsec_capi.py infra/crowdsec/config.yaml.local
git commit -m "feat(crowdsec): the community-blocklist switch as a write-register-restart-verify flow

The override goes on disk before cscli capi register, because cscli refuses
to register without it; and a missing credentials file is never referenced,
because CrowdSec will not start if it is.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The Celery tasks, the lock, the tick, and the beat entry

**Files:**
- Modify: `backend/app/tasks/crowdsec.py`, `backend/app/core/celery_app.py`
- Test: `backend/tests/test_crowdsec_update_tasks.py`

**Interfaces:**
- Produces (tasks, by name): `app.tasks.crowdsec.update_hub(trigger="manual")`, `app.tasks.crowdsec.hub_update_tick()`, `app.tasks.crowdsec.apply_capi()`; helpers `HUB_LOCK_KEY = "megoopm:crowdsec:hub-update"`, `CAPI_LOCK_KEY = "megoopm:crowdsec:capi-apply"`, `_lock_client()`, `_container_exec(argv)`, `_container_restart()`, `_load_maintenance_settings(conn) -> MaintenanceSettings`.
- Beat: `hub-update-tick-hourly` → `app.tasks.crowdsec.hub_update_tick`, `crontab(minute=5)`, `expires: 3000`. HA routes: all three tasks to `node_queue(settings.crowdsec_control_node_id)` when it is set.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_crowdsec_update_tasks.py`:

```python
"""The tasks: settings in, callables faked, run record out."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from app.models.crowdsec_job_run import CrowdSecJobRun
from app.models.enums import CrowdSecJobKind, HubUpdateFrequency
from app.models.instance_settings import InstanceSettings
from app.services.crowdsec import hub
from app.services.crowdsec.job_run import read_job_run
from app.services.crowdsec.reload import ExecResult
from app.tasks import crowdsec as tasks
from sqlalchemy import create_engine, insert


class FakeLock:
    """A lock that is free unless told otherwise."""

    def __init__(self, held: bool = False) -> None:
        self.held = held

    def acquire(self, blocking: bool = False) -> bool:
        return not self.held

    def release(self) -> None:
        pass


class FakeRedis:
    def __init__(self, held: bool = False) -> None:
        self._lock = FakeLock(held)

    def lock(self, name: str, timeout: int | None = None) -> FakeLock:
        return self._lock

    def close(self) -> None:
        pass


LIST = json.dumps({"collections": [{"name": "crowdsecurity/nginx", "local_version": "0.2"}]})
LIST2 = json.dumps({"collections": [{"name": "crowdsecurity/nginx", "local_version": "0.3"}]})


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Iterator:
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    for table in (InstanceSettings.__table__, CrowdSecJobRun.__table__):
        table.create(eng)
    with eng.begin() as conn:
        conn.execute(
            insert(InstanceSettings.__table__).values(
                id=1,
                default_site_mode="not_found",
                crowdsec_ban_mode="default",
                crowdsec_hub_auto_update=True,
                crowdsec_hub_update_frequency="daily",
                crowdsec_hub_update_weekday=6,
                crowdsec_hub_update_hour_utc=3,
                crowdsec_capi_enabled=False,
            )
        )
    monkeypatch.setattr(tasks, "sync_engine", lambda: eng)
    yield eng
    eng.dispose()


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Fake the container, the restart, the health wait, the lock, the file."""
    state = {"lists": [LIST, LIST], "restarts": 0, "ran": []}

    def fake_exec(argv):
        state["ran"].append(argv)
        if argv == hub.CMD_LIST:
            return ExecResult(0, state["lists"].pop(0))
        if argv == hub.CMD_VERSION:
            return ExecResult(0, "version: v1.6.4-x")
        return ExecResult(0, "")

    def fake_restart():
        state["restarts"] += 1

    monkeypatch.setattr(tasks, "_container_exec", fake_exec)
    monkeypatch.setattr(tasks, "_container_restart", fake_restart)
    monkeypatch.setattr(tasks, "_wait_for_lapi", lambda: True)
    monkeypatch.setattr(tasks, "_lock_client", lambda: FakeRedis())
    monkeypatch.setattr(tasks.settings, "crowdsec_config_local_path", str(tmp_path / "config.yaml.local"))
    return state


def test_update_hub_records_a_run(engine, fakes) -> None:
    out = tasks.update_hub.run("manual")
    assert out["ok"] is True and out["restarted"] is False
    with engine.begin() as conn:
        row = read_job_run(conn, CrowdSecJobKind.hub_update)
    assert row is not None and row.ok and row.finished_at is not None
    assert row.trigger.value == "manual" and row.detail["agent_version"] == "v1.6.4"


def test_update_hub_restarts_when_something_changed(engine, fakes) -> None:
    fakes["lists"] = [LIST, LIST2]
    out = tasks.update_hub.run("scheduled")
    assert out["restarted"] is True and out["updated"] == ["collections:crowdsecurity/nginx"]
    assert fakes["restarts"] == 1


def test_update_hub_skips_when_the_lock_is_held(engine, fakes, monkeypatch) -> None:
    monkeypatch.setattr(tasks, "_lock_client", lambda: FakeRedis(held=True))
    out = tasks.update_hub.run("manual")
    assert out == {"ran": False, "reason": "already running"}
    with engine.begin() as conn:
        assert read_job_run(conn, CrowdSecJobKind.hub_update) is None


def test_tick_runs_only_when_due(engine, fakes, monkeypatch) -> None:
    monkeypatch.setattr(tasks, "_now", lambda: datetime(2026, 9, 4, 3, 5, tzinfo=UTC))
    assert tasks.hub_update_tick.run()["ran"] is True
    # Same hour again: the run record says it already happened.
    assert tasks.hub_update_tick.run() == {"ran": False, "reason": "already ran this hour"}
    monkeypatch.setattr(tasks, "_now", lambda: datetime(2026, 9, 4, 9, 5, tzinfo=UTC))
    assert tasks.hub_update_tick.run() == {"ran": False, "reason": "not the configured hour"}


def test_tick_respects_the_switch(engine, fakes, monkeypatch) -> None:
    with engine.begin() as conn:
        conn.execute(InstanceSettings.__table__.update().values(crowdsec_hub_auto_update=False))
    monkeypatch.setattr(tasks, "_now", lambda: datetime(2026, 9, 4, 3, 5, tzinfo=UTC))
    assert tasks.hub_update_tick.run() == {"ran": False, "reason": "auto-update is off"}


def test_apply_capi_reads_the_desired_state_and_records(engine, fakes, tmp_path) -> None:
    with engine.begin() as conn:
        conn.execute(InstanceSettings.__table__.update().values(crowdsec_capi_enabled=True))
    out = tasks.apply_capi.run()
    assert out["ok"] is True and out["enabled"] is True
    assert "online_client" in (tmp_path / "config.yaml.local").read_text(encoding="utf-8")
    with engine.begin() as conn:
        row = read_job_run(conn, CrowdSecJobKind.capi_apply)
    assert row is not None and row.ok and row.detail == {"enabled": True}


def test_maintenance_settings_loader_maps_the_enum(engine) -> None:
    with engine.begin() as conn:
        s = tasks._load_maintenance_settings(conn)
    assert s.frequency is HubUpdateFrequency.daily and s.hour_utc == 3 and s.capi_enabled is False
```

- [ ] **Step 2: Run them to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_crowdsec_update_tasks.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — `AttributeError: module 'app.tasks.crowdsec' has no attribute 'update_hub'`.

- [ ] **Step 3: The tasks**

Append to `backend/app/tasks/crowdsec.py` (extend the imports: `import redis` (sync client), `from datetime import UTC, datetime`, `from app.models.enums import CrowdSecJobKind, CrowdSecJobTrigger, HubUpdateFrequency`, `from app.models.instance_settings import InstanceSettings`, `from app.services.crowdsec import capi, hub`, `from app.services.crowdsec.job_run import finish_job_run, read_job_run, start_job_run`, `from app.services.crowdsec.reload import ExecResult, exec_in_container`):

```python
# --- maintenance: hub refresh and the community blocklist ------------------------

HUB_LOCK_KEY = "megoopm:crowdsec:hub-update"
CAPI_LOCK_KEY = "megoopm:crowdsec:capi-apply"
#: Both jobs talk to the internet and restart a container; well under this.
_LOCK_TIMEOUT_S = 900
_EXEC_TIMEOUT_S = 120


@dataclass(frozen=True, slots=True)
class MaintenanceSettings:
    auto_update: bool
    frequency: HubUpdateFrequency
    weekday: int
    hour_utc: int
    capi_enabled: bool


def _load_maintenance_settings(conn: Connection) -> MaintenanceSettings:
    table = InstanceSettings.__table__
    row = conn.execute(select(table).where(table.c.id == 1)).one()
    return MaintenanceSettings(
        auto_update=row.crowdsec_hub_auto_update,
        frequency=HubUpdateFrequency(row.crowdsec_hub_update_frequency),
        weekday=row.crowdsec_hub_update_weekday,
        hour_utc=row.crowdsec_hub_update_hour_utc,
        capi_enabled=row.crowdsec_capi_enabled,
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _lock_client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url)


def _container_exec(argv: list[str]) -> ExecResult:
    return exec_in_container(
        settings.crowdsec_container_name,
        argv,
        socket_path=settings.docker_socket_path,
        timeout_seconds=_EXEC_TIMEOUT_S,
    )


def _container_restart() -> None:
    restart_container(
        settings.crowdsec_container_name,
        socket_path=settings.docker_socket_path,
        timeout_seconds=settings.crowdsec_reload_health_timeout_seconds,
    )


def _run_locked(key: str, fn: Callable[[], dict]) -> dict:
    """Run ``fn`` under a Redis lock, or report that it is already running."""
    client = _lock_client()
    lock = client.lock(key, timeout=_LOCK_TIMEOUT_S)
    try:
        if not lock.acquire(blocking=False):
            return {"ran": False, "reason": "already running"}
        try:
            return fn()
        finally:
            lock.release()
    finally:
        client.close()


@celery_app.task(name="app.tasks.crowdsec.update_hub")
def update_hub(trigger: str = "manual") -> dict:
    """Refresh hub items; restart only if something changed; record the outcome."""

    def _go() -> dict:
        engine = sync_engine()
        try:
            with engine.begin() as conn:
                start_job_run(
                    conn,
                    CrowdSecJobKind.hub_update,
                    trigger=CrowdSecJobTrigger(trigger),
                    started_at=_now(),
                )
            result = hub.run_hub_update(
                exec=_container_exec, restart=_container_restart, healthy=_wait_for_lapi
            )
            with engine.begin() as conn:
                finish_job_run(
                    conn,
                    CrowdSecJobKind.hub_update,
                    ok=result.ok,
                    error=result.error,
                    restarted=result.restarted,
                    detail={
                        "updated": result.updated,
                        "agent_version": result.agent_version,
                        "latest_agent_version": result.latest_agent_version,
                    },
                    finished_at=_now(),
                )
            return result.as_dict()
        finally:
            engine.dispose()

    return _run_locked(HUB_LOCK_KEY, _go)


@celery_app.task(name="app.tasks.crowdsec.hub_update_tick")
def hub_update_tick() -> dict:
    """Hourly: run the hub refresh if this is the configured slot."""
    engine = sync_engine()
    try:
        with engine.begin() as conn:
            conf = _load_maintenance_settings(conn)
            last = read_job_run(conn, CrowdSecJobKind.hub_update)
    finally:
        engine.dispose()
    due, reason = hub.is_due(
        now=_now(),
        auto_update=conf.auto_update,
        frequency=conf.frequency,
        weekday=conf.weekday,
        hour_utc=conf.hour_utc,
        last_started_at=last.started_at if last else None,
    )
    if not due:
        return {"ran": False, "reason": reason}
    outcome = update_hub.run("scheduled")
    return {"ran": True, **outcome}


@celery_app.task(name="app.tasks.crowdsec.apply_capi")
def apply_capi() -> dict:
    """Make the container's config match the desired blocklist state."""

    def _go() -> dict:
        engine = sync_engine()
        try:
            with engine.begin() as conn:
                conf = _load_maintenance_settings(conn)
                start_job_run(
                    conn, CrowdSecJobKind.capi_apply, trigger=CrowdSecJobTrigger.manual, started_at=_now()
                )
            result = capi.run_capi_apply(
                enabled=conf.capi_enabled,
                path=Path(settings.crowdsec_config_local_path),
                exec=_container_exec,
                restart=_container_restart,
                healthy=_wait_for_lapi,
            )
            with engine.begin() as conn:
                finish_job_run(
                    conn,
                    CrowdSecJobKind.capi_apply,
                    ok=result.ok,
                    error=result.error,
                    restarted=result.restarted,
                    detail={"enabled": result.enabled},
                    finished_at=_now(),
                )
            return result.as_dict()
        finally:
            engine.dispose()

    return _run_locked(CAPI_LOCK_KEY, _go)
```

Extend `__all__` with the three task names and `HUB_LOCK_KEY`, `CAPI_LOCK_KEY`.


- [ ] **Step 4: Beat and routes**

In `backend/app/core/celery_app.py`, inside the `beat_schedule` dict after
`prune-visitor-days`:

```python
        "hub-update-tick-hourly": {
            "task": "app.tasks.crowdsec.hub_update_tick",
            # The tick decides whether this hour is the configured slot; a
            # tick that could not run within the hour is worthless.
            "schedule": crontab(minute=5),
            "options": {"expires": 3000},
        },
```

In `_configure_ha`, where `task_routes` is built, add the three maintenance
tasks to the control-plane node's queue when it is configured:

```python
    if settings.crowdsec_control_node_id:
        control_queue = node_queue(settings.crowdsec_control_node_id)
        for name in (
            "app.tasks.crowdsec.hub_update_tick",
            "app.tasks.crowdsec.update_hub",
            "app.tasks.crowdsec.apply_capi",
        ):
            celery_app.conf.task_routes[name] = {"queue": control_queue}
```

(Read `_configure_ha` first: `task_routes` is assigned as a dict literal;
append this block right after that assignment. When
`crowdsec_control_node_id` is unset in HA the tasks stay on the default
queue, which under HA nobody consumes — the same fail-safe as whitelists,
and the API refuses with 409 in that case.)

- [ ] **Step 5: Run, lint, commit**

```bash
docker exec megoopm-test ruff format app/tasks/crowdsec.py app/core/celery_app.py app/services/crowdsec/hub.py tests/test_crowdsec_update_tasks.py
docker exec megoopm-test python -m pytest tests/test_crowdsec_update_tasks.py tests/test_crowdsec_hub.py tests/test_crowdsec_reload.py tests/test_tasks.py tests/test_analytics_tasks.py -p no:cacheprovider -p no:warnings
docker exec megoopm-test ruff check app tests
```
Commit separately:
```bash
git add backend/app/tasks/crowdsec.py backend/app/core/celery_app.py backend/app/services/crowdsec/hub.py backend/tests/test_crowdsec_update_tasks.py
git commit -m "feat(crowdsec): the hub refresh and blocklist tasks, the lock, and the hourly tick

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Settings and maintenance routes

**Files:**
- Modify: `backend/app/schemas/instance_settings.py`, `backend/app/schemas/crowdsec.py`, `backend/app/services/instance_settings.py`, `backend/app/api/routes/settings.py`, `backend/app/api/routes/crowdsec.py`, `backend/openapi.json`
- Test: `backend/tests/test_crowdsec_maintenance_api.py`

**Interfaces:**
- Produces:
  - `InstanceSettingsRead` gains `crowdsec_hub_auto_update: bool`, `crowdsec_hub_update_frequency: HubUpdateFrequency`, `crowdsec_hub_update_weekday: int`, `crowdsec_hub_update_hour_utc: int`, `crowdsec_capi_enabled: bool`
  - `CrowdSecHubUpdate(auto_update: bool, frequency: HubUpdateFrequency, weekday: int 0–6, hour_utc: int 0–23)`; `CrowdSecCapiUpdate(enabled: bool)`
  - `CrowdSecJobRunRead(kind, started_at, finished_at, ok, error, trigger, restarted, detail)`; `CrowdSecMaintenance(hub: CrowdSecJobRunRead | None, capi: CrowdSecJobRunRead | None, reload_configured: bool, running: dict[str, bool])`
  - `PATCH /settings/crowdsec-hub` → `InstanceSettingsRead`; `PATCH /settings/crowdsec-capi` → 202 `InstanceSettingsRead` (409 when reloads are not configured, settings still saved); `GET /crowdsec/maintenance`; `POST /crowdsec/hub/update` → 202 `{"queued": true}` / 409.
  - `settings_service.update_crowdsec_hub(db, changes)`, `settings_service.update_crowdsec_capi(db, enabled)`.
  - `routes/crowdsec._job_running(key) -> bool` (async, via `app.core.redis.redis_client`), `_enqueue_control_task(name, **kwargs) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_crowdsec_maintenance_api.py`:

```python
"""The Updates tab's routes. SQLite-backed; Redis and the broker are faked."""

from __future__ import annotations

import pytest
from app.api.routes import crowdsec as crowdsec_routes
from app.models.crowdsec_job_run import CrowdSecJobRun
from app.models.enums import CrowdSecJobKind, CrowdSecJobTrigger
from httpx import AsyncClient

SETTINGS = "/api/v1/settings"
MAINT = "/api/v1/crowdsec/maintenance"
UPDATE = "/api/v1/crowdsec/hub/update"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class FakeRedis:
    def __init__(self, keys: set[str] | None = None) -> None:
        self.keys = keys or set()

    async def exists(self, *names: str) -> int:
        return sum(1 for n in names if n in self.keys)

    async def aclose(self) -> None:
        pass


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        crowdsec_routes.celery_app, "send_task", lambda name, **kw: calls.append((name, kw))
    )
    monkeypatch.setattr(crowdsec_routes, "redis_client", lambda: FakeRedis())
    return calls


# --- settings --------------------------------------------------------------------


async def test_settings_expose_the_defaults(db_client: AsyncClient, admin_token: str, sent) -> None:
    body = (await db_client.get(SETTINGS, headers=_auth(admin_token))).json()
    assert body["crowdsec_hub_auto_update"] is True
    assert body["crowdsec_hub_update_frequency"] == "daily"
    assert body["crowdsec_hub_update_weekday"] == 6
    assert body["crowdsec_hub_update_hour_utc"] == 3
    assert body["crowdsec_capi_enabled"] is False


async def test_patch_hub_schedule(db_client: AsyncClient, admin_token: str, sent) -> None:
    resp = await db_client.patch(
        f"{SETTINGS}/crowdsec-hub",
        headers=_auth(admin_token),
        json={"auto_update": True, "frequency": "weekly", "weekday": 2, "hour_utc": 22},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["crowdsec_hub_update_frequency"] == "weekly"
    assert resp.json()["crowdsec_hub_update_weekday"] == 2
    assert resp.json()["crowdsec_hub_update_hour_utc"] == 22


@pytest.mark.parametrize(
    "payload",
    [
        {"auto_update": True, "frequency": "daily", "weekday": 7, "hour_utc": 3},
        {"auto_update": True, "frequency": "daily", "weekday": 0, "hour_utc": 24},
        {"auto_update": True, "frequency": "hourly", "weekday": 0, "hour_utc": 3},
    ],
)
async def test_patch_hub_schedule_validates(db_client: AsyncClient, admin_token: str, sent, payload) -> None:
    resp = await db_client.patch(f"{SETTINGS}/crowdsec-hub", headers=_auth(admin_token), json=payload)
    assert resp.status_code == 422


async def test_patch_capi_saves_and_enqueues(db_client: AsyncClient, admin_token: str, sent) -> None:
    resp = await db_client.patch(
        f"{SETTINGS}/crowdsec-capi", headers=_auth(admin_token), json={"enabled": True}
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["crowdsec_capi_enabled"] is True
    assert sent == [("app.tasks.crowdsec.apply_capi", {})]


async def test_settings_patches_are_admin_only(db_client: AsyncClient, member_token: str, sent) -> None:
    assert (
        await db_client.patch(f"{SETTINGS}/crowdsec-capi", headers=_auth(member_token), json={"enabled": True})
    ).status_code == 403


# --- maintenance status + update now ----------------------------------------------


async def test_maintenance_is_empty_at_first(db_client: AsyncClient, admin_token: str, sent) -> None:
    body = (await db_client.get(MAINT, headers=_auth(admin_token))).json()
    assert body == {
        "hub": None,
        "capi": None,
        "reload_configured": True,
        "running": {"hub": False, "capi": False},
    }


async def test_maintenance_reports_the_last_runs(
    db_client: AsyncClient, admin_token: str, sent, session_factory
) -> None:
    async with session_factory() as db:
        db.add(
            CrowdSecJobRun(
                kind=CrowdSecJobKind.hub_update,
                ok=True,
                trigger=CrowdSecJobTrigger.scheduled,
                restarted=True,
                detail={"updated": ["collections:crowdsecurity/nginx"], "agent_version": "v1.6.4", "latest_agent_version": "v1.8.0"},
            )
        )
        await db.commit()
    body = (await db_client.get(MAINT, headers=_auth(admin_token))).json()
    assert body["hub"]["ok"] is True
    assert body["hub"]["detail"]["latest_agent_version"] == "v1.8.0"
    assert body["hub"]["trigger"] == "scheduled"
    assert body["capi"] is None


async def test_maintenance_says_when_a_job_is_running(
    db_client: AsyncClient, admin_token: str, sent, monkeypatch
) -> None:
    monkeypatch.setattr(crowdsec_routes, "redis_client", lambda: FakeRedis({"megoopm:crowdsec:hub-update"}))
    body = (await db_client.get(MAINT, headers=_auth(admin_token))).json()
    assert body["running"] == {"hub": True, "capi": False}


async def test_update_now_enqueues_a_manual_run(db_client: AsyncClient, admin_token: str, sent) -> None:
    resp = await db_client.post(UPDATE, headers=_auth(admin_token))
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"queued": True}
    assert sent == [("app.tasks.crowdsec.update_hub", {"kwargs": {"trigger": "manual"}})]


async def test_update_now_is_409_while_running(db_client: AsyncClient, admin_token: str, sent, monkeypatch) -> None:
    monkeypatch.setattr(crowdsec_routes, "redis_client", lambda: FakeRedis({"megoopm:crowdsec:hub-update"}))
    resp = await db_client.post(UPDATE, headers=_auth(admin_token))
    assert resp.status_code == 409
    assert resp.json()["detail"] == "An update is already running."


async def test_update_now_is_409_when_reloads_are_not_configured(
    db_client: AsyncClient, admin_token: str, sent, monkeypatch
) -> None:
    monkeypatch.setattr(crowdsec_routes.settings, "ha_enabled", True)
    monkeypatch.setattr(crowdsec_routes.settings, "crowdsec_control_node_id", None)
    resp = await db_client.post(UPDATE, headers=_auth(admin_token))
    assert resp.status_code == 409
    assert "CROWDSEC_CONTROL_NODE_ID" in resp.json()["detail"]
    assert sent == []


async def test_update_now_is_admin_only(db_client: AsyncClient, member_token: str, sent) -> None:
    assert (await db_client.post(UPDATE, headers=_auth(member_token))).status_code == 403
```

- [ ] **Step 2: Run them to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_crowdsec_maintenance_api.py -p no:cacheprovider -p no:warnings
```
Expected: FAIL — `KeyError: 'crowdsec_hub_auto_update'`, 404s.

- [ ] **Step 3: Schemas and service**

In `backend/app/schemas/instance_settings.py`: import `HubUpdateFrequency`;
add the five fields to `InstanceSettingsRead` after `app_url` and to
`from_row`:

```python
    crowdsec_hub_auto_update: bool
    crowdsec_hub_update_frequency: HubUpdateFrequency
    crowdsec_hub_update_weekday: int
    crowdsec_hub_update_hour_utc: int
    crowdsec_capi_enabled: bool
```
```python
            crowdsec_hub_auto_update=row.crowdsec_hub_auto_update,
            crowdsec_hub_update_frequency=row.crowdsec_hub_update_frequency,
            crowdsec_hub_update_weekday=row.crowdsec_hub_update_weekday,
            crowdsec_hub_update_hour_utc=row.crowdsec_hub_update_hour_utc,
            crowdsec_capi_enabled=row.crowdsec_capi_enabled,
```

After `CrowdSecBanUpdate`:

```python
class CrowdSecHubUpdate(BaseModel):
    """The hub refresh schedule. ``hour_utc`` is UTC; the UI converts."""

    auto_update: bool
    frequency: HubUpdateFrequency
    weekday: int = Field(ge=0, le=6, description="Monday = 0; used when weekly")
    hour_utc: int = Field(ge=0, le=23)


class CrowdSecCapiUpdate(BaseModel):
    """Desired state of the community blocklist. Applying it restarts CrowdSec."""

    enabled: bool
```

Add both to `__all__`.

In `backend/app/schemas/crowdsec.py`:

```python
class CrowdSecJobRunRead(BaseModel):
    """The last run of one maintenance job."""

    model_config = ConfigDict(from_attributes=True)

    kind: CrowdSecJobKind
    started_at: datetime
    finished_at: datetime | None
    ok: bool
    error: str | None
    trigger: CrowdSecJobTrigger
    restarted: bool
    detail: dict[str, Any]


class CrowdSecMaintenance(BaseModel):
    """What the Updates tab needs in one call."""

    hub: CrowdSecJobRunRead | None
    capi: CrowdSecJobRunRead | None
    reload_configured: bool
    running: dict[str, bool]
```

(Check that file's imports for `ConfigDict`, `datetime`, `Any`; add the
enum imports.) Add both to `__all__`.

In `backend/app/services/instance_settings.py`:

```python
async def update_crowdsec_hub(db: AsyncSession, changes: dict[str, Any]) -> InstanceSettings:
    """Apply the hub refresh schedule."""
    row = await get_instance_settings(db)
    row.crowdsec_hub_auto_update = changes["auto_update"]
    row.crowdsec_hub_update_frequency = changes["frequency"]
    row.crowdsec_hub_update_weekday = changes["weekday"]
    row.crowdsec_hub_update_hour_utc = changes["hour_utc"]
    await db.commit()
    await db.refresh(row)
    return row


async def update_crowdsec_capi(db: AsyncSession, *, enabled: bool) -> InstanceSettings:
    """Record the desired blocklist state. The apply task makes it real."""
    row = await get_instance_settings(db)
    row.crowdsec_capi_enabled = enabled
    await db.commit()
    await db.refresh(row)
    return row
```

- [ ] **Step 4: Routes**

In `backend/app/api/routes/settings.py` (import `CrowdSecCapiUpdate,
CrowdSecHubUpdate`; `from app.api.routes.crowdsec import RELOADS_NOT_CONFIGURED,
enqueue_control_task`; `from app.services import audit as audit_service`):

```python
@router.patch("/crowdsec-hub", response_model=InstanceSettingsRead)
async def update_crowdsec_hub_settings(
    body: CrowdSecHubUpdate, admin: AdminUser, db: SessionDep
) -> InstanceSettingsRead:
    """The hub refresh schedule. Admin-only. Takes effect at the next hourly tick."""
    row = await settings_service.update_crowdsec_hub(db, body.model_dump())
    await audit_service.record_audit(
        db,
        actor=admin.email,
        action=AuditAction.update,
        object_type="instance_settings",
        object_id=row.id,
        meta={"crowdsec_hub": body.model_dump(mode="json")},
    )
    await db.commit()
    return InstanceSettingsRead.from_row(row)


@router.patch(
    "/crowdsec-capi", response_model=InstanceSettingsRead, status_code=status.HTTP_202_ACCEPTED
)
async def update_crowdsec_capi_settings(
    body: CrowdSecCapiUpdate, admin: AdminUser, db: SessionDep
) -> InstanceSettingsRead:
    """Desired state of the community blocklist; enqueues the apply. Admin-only.

    Saved even when the apply cannot be enqueued, so the choice is not lost;
    the 409 tells the operator why nothing happened.
    """
    row = await settings_service.update_crowdsec_capi(db, enabled=body.enabled)
    await audit_service.record_audit(
        db,
        actor=admin.email,
        action=AuditAction.enable if body.enabled else AuditAction.disable,
        object_type="instance_settings",
        object_id=row.id,
        meta={"crowdsec_capi_enabled": body.enabled},
    )
    await db.commit()
    if not enqueue_control_task("app.tasks.crowdsec.apply_capi"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=RELOADS_NOT_CONFIGURED)
    return InstanceSettingsRead.from_row(row)
```

In `backend/app/api/routes/crowdsec.py`: import `redis_client` from
`app.core.redis`, `CrowdSecJobRun`, `CrowdSecJobKind`, the two new schemas,
and `HUB_LOCK_KEY`, `CAPI_LOCK_KEY` from `app.tasks.crowdsec`. Rename the
existing private helpers into public ones the settings router can import,
keeping the old names as aliases:

```python
RELOADS_NOT_CONFIGURED = (
    "CrowdSec reloads are not configured: set CROWDSEC_CONTROL_NODE_ID "
    "to the node whose worker has the docker socket (HA only; a "
    "single-node deployment needs no node id)."
)


def reload_configured() -> bool:
    return _reload_configured()


def enqueue_control_task(name: str, **kwargs) -> bool:
    """Send ``name`` to the worker that holds the docker socket, or say it cannot."""
    if not _reload_configured():
        return False
    if not settings.ha_enabled:
        celery_app.send_task(name, **kwargs)
        return True
    celery_app.send_task(name, queue=node_queue(settings.crowdsec_control_node_id), **kwargs)
    return True


async def _job_running(key: str) -> bool:
    client = redis_client()
    try:
        return bool(await client.exists(key))
    finally:
        await client.aclose()


@router.get("/maintenance", response_model=CrowdSecMaintenance)
async def maintenance(_: AdminUser, db: SessionDep) -> CrowdSecMaintenance:
    """Both maintenance jobs' last runs, and whether one is running now."""
    hub_row = await db.get(CrowdSecJobRun, CrowdSecJobKind.hub_update)
    capi_row = await db.get(CrowdSecJobRun, CrowdSecJobKind.capi_apply)
    return CrowdSecMaintenance(
        hub=CrowdSecJobRunRead.model_validate(hub_row) if hub_row else None,
        capi=CrowdSecJobRunRead.model_validate(capi_row) if capi_row else None,
        reload_configured=_reload_configured(),
        running={"hub": await _job_running(HUB_LOCK_KEY), "capi": await _job_running(CAPI_LOCK_KEY)},
    )


@router.post("/hub/update", status_code=status.HTTP_202_ACCEPTED)
async def hub_update_now(admin: AdminUser, db: SessionDep) -> dict[str, bool]:
    """Refresh the hub now. 409 while a run is in progress or reloads are unwired."""
    if await _job_running(HUB_LOCK_KEY):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An update is already running.")
    if not enqueue_control_task("app.tasks.crowdsec.update_hub", kwargs={"trigger": "manual"}):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=RELOADS_NOT_CONFIGURED)
    await audit_service.record_audit(
        db, actor=admin.email, action=AuditAction.update, object_type="crowdsec_hub", meta={"update_now": True}
    )
    await db.commit()
    return {"queued": True}
```

Replace the body of `_enqueue_apply` with
`return enqueue_control_task("app.tasks.crowdsec.apply_crowdsec_whitelists")`
and make the existing `apply_whitelists` 503 detail use
`RELOADS_NOT_CONFIGURED` (same text). The `test_update_now_is_409_when…`
test asserts the string mentions `CROWDSEC_CONTROL_NODE_ID`.

Importing `app.tasks.crowdsec` from a route module pulls Celery task
definitions into the API process; that is already the case for
`apply_crowdsec_whitelists` via `celery_app`, so nothing new is loaded. If
`ruff` flags a cycle, move the two lock keys to
`app/services/crowdsec/hub.py` and `capi.py` and import them from there.

- [ ] **Step 5: Run, regenerate, full suite, commit**

```bash
docker exec megoopm-test ruff format app/schemas app/services/instance_settings.py app/api/routes/settings.py app/api/routes/crowdsec.py tests/test_crowdsec_maintenance_api.py
docker exec megoopm-test python -m pytest tests/test_crowdsec_maintenance_api.py tests/test_crowdsec_whitelists_api.py tests/test_settings_api.py -p no:cacheprovider -p no:warnings
docker exec megoopm-test python -m scripts.export_openapi
docker exec megoopm-test ruff check app tests
docker exec megoopm-test python -m pytest -p no:cacheprovider -p no:warnings
```
Commit separately:
```bash
git add backend/app/schemas backend/app/services/instance_settings.py backend/app/api/routes backend/tests/test_crowdsec_maintenance_api.py backend/openapi.json
git commit -m "feat(crowdsec): schedule and blocklist settings, maintenance status, Update now

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: The Updates tab

**Files:**
- Modify: `frontend/src/lib/api/generated/schema.ts` (regenerated), `frontend/src/lib/api/resources/settings.ts`, `frontend/src/lib/api/resources/crowdsec.ts`, `frontend/src/lib/api/index.ts`
- Modify: the six test files holding `InstanceSettings` fixtures
- Create: `frontend/src/components/security/updates-lib.ts`, `hub-updates-card.tsx`, `blocklist-card.tsx`, `updates-tab.tsx`
- Modify: `frontend/src/components/security/security-view.tsx`
- Test: `frontend/src/components/security/updates-lib.test.ts`, `frontend/src/components/security/updates-tab.test.tsx`

**Interfaces:**
- Produces:
  - `instanceSettings.updateCrowdSecHub(body)`, `instanceSettings.updateCrowdSecCapi(body)`; types `CrowdSecHubUpdate`, `CrowdSecCapiUpdate`, `HubUpdateFrequency`
  - `crowdsec.maintenance()`, `crowdsec.hubUpdateNow()`; types `CrowdSecMaintenance`, `CrowdSecJobRun`
  - `updates-lib.ts`: `utcHourToLocal(hourUtc: number, now?: Date): number`, `localHourToUtc(hourLocal: number, now?: Date): number`, `describeHubRun(run: CrowdSecJobRun | null): string`, `describeCapiRun(desired: boolean, run: CrowdSecJobRun | null, running: boolean): { label: string; failed: boolean }`, `WEEKDAYS`
  - `UpdatesTab()` — self-loading; `HubUpdatesCard({ settings, status, running, reloadConfigured, onSaved, onQueued })`; `BlocklistCard({ desired, run, running, reloadConfigured, onChanged })`

- [ ] **Step 1: Regenerate, add the calls, fix fixtures**

```bash
cd frontend && npm run gen:api
```

`settings.ts`:

```ts
export type CrowdSecHubUpdate = Schemas["CrowdSecHubUpdate"];
export type CrowdSecCapiUpdate = Schemas["CrowdSecCapiUpdate"];
export type HubUpdateFrequency = Schemas["HubUpdateFrequency"];
```
```ts
  /** The hub refresh schedule; takes effect at the next hourly tick. */
  updateCrowdSecHub: (body: CrowdSecHubUpdate) =>
    api.patch<InstanceSettings>(`${BASE}/crowdsec-hub`, body),
  /** Desired blocklist state; 202 and an apply is queued (CrowdSec restarts). */
  updateCrowdSecCapi: (body: CrowdSecCapiUpdate) =>
    api.patch<InstanceSettings>(`${BASE}/crowdsec-capi`, body),
```

`crowdsec.ts`:

```ts
export type CrowdSecMaintenance = Schemas["CrowdSecMaintenance"];
export type CrowdSecJobRun = Schemas["CrowdSecJobRunRead"];
```
```ts
  /** Both maintenance jobs' last runs, and whether one is running now. */
  maintenance: () => api.get<CrowdSecMaintenance>(`${BASE}/maintenance`),
  /** 202 and a run is queued; 409 while one is running or reloads are unwired. */
  hubUpdateNow: () => api.post<{ queued: boolean }>(`${BASE}/hub/update`, {}),
```

Re-export the new types from `src/lib/api/index.ts`.

The generated `InstanceSettings` now requires five more fields. In each of
`custom-page-editor-view.test.tsx`, `settings/ban-page-card.test.tsx`,
`settings/lib.test.ts`, `settings/llm-card.test.tsx`,
`settings/settings-view.test.tsx`, `settings/smtp-card.test.tsx`, every
fixture object that has `smtp_from_name:` gains:

```ts
  crowdsec_hub_auto_update: true,
  crowdsec_hub_update_frequency: "daily" as const,
  crowdsec_hub_update_weekday: 6,
  crowdsec_hub_update_hour_utc: 3,
  crowdsec_capi_enabled: false,
```

Apply with a script that inserts after each `smtp_from_name: …,` line
(the P4 fixture script is the model), then `npx tsc --noEmit` must be clean.

- [ ] **Step 2: Write the failing tests**

Create `frontend/src/components/security/updates-lib.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import {
  describeCapiRun,
  describeHubRun,
  localHourToUtc,
  utcHourToLocal,
} from "@/components/security/updates-lib";

// A fixed instant whose local offset the test computes itself, so it holds
// in any timezone the CI box runs in.
const NOW = new Date("2026-09-04T12:00:00Z");
const OFFSET_HOURS = -NOW.getTimezoneOffset() / 60;

describe("hour conversion", () => {
  it("round-trips through local time", () => {
    for (let h = 0; h < 24; h++) {
      expect(localHourToUtc(utcHourToLocal(h, NOW), NOW)).toBe(h);
    }
  });

  it("applies the browser's offset", () => {
    expect(utcHourToLocal(3, NOW)).toBe((((3 + OFFSET_HOURS) % 24) + 24) % 24);
  });
});

const run = (over: Record<string, unknown>) => ({
  kind: "hub_update" as const,
  started_at: "2026-09-04T03:05:00Z",
  finished_at: "2026-09-04T03:06:00Z",
  ok: true,
  error: null,
  trigger: "scheduled" as const,
  restarted: false,
  detail: {},
  ...over,
});

describe("describeHubRun", () => {
  it("has never run", () => {
    expect(describeHubRun(null)).toMatch(/never run/i);
  });
  it("no changes", () => {
    expect(describeHubRun(run({ detail: { updated: [] } }))).toMatch(/no changes/i);
  });
  it("counts updates and mentions the restart", () => {
    const text = describeHubRun(run({ restarted: true, detail: { updated: ["a", "b"] } }));
    expect(text).toMatch(/2 items updated/i);
    expect(text).toMatch(/restarted/i);
  });
  it("shows the error", () => {
    expect(describeHubRun(run({ ok: false, error: "hub upgrade failed: x" }))).toMatch(/hub upgrade failed/);
  });
  it("says it is running", () => {
    expect(describeHubRun(run({ finished_at: null }))).toMatch(/running/i);
  });
});

describe("describeCapiRun", () => {
  it("off with nothing applied", () => {
    expect(describeCapiRun(false, null, false)).toEqual({ label: "Off", failed: false });
  });
  it("turning on while running", () => {
    expect(describeCapiRun(true, null, true).label).toMatch(/turning on/i);
  });
  it("on once applied", () => {
    expect(describeCapiRun(true, run({ kind: "capi_apply", detail: { enabled: true } }), false)).toEqual({
      label: "On",
      failed: false,
    });
  });
  it("failed keeps the error", () => {
    const r = describeCapiRun(true, run({ kind: "capi_apply", ok: false, error: "no route", detail: { enabled: false } }), false);
    expect(r.failed).toBe(true);
    expect(r.label).toMatch(/no route/);
  });
});
```

Create `frontend/src/components/security/updates-tab.test.tsx`:

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { crowdsec, instanceSettings } from "@/lib/api";
import { ApiError } from "@/lib/api/errors";
import { UpdatesTab } from "@/components/security/updates-tab";

const SETTINGS = {
  default_site_mode: "not_found" as const,
  default_site_redirect_url: null,
  default_site_page_id: null,
  crowdsec_ban_mode: "default" as const,
  crowdsec_ban_page_id: null,
  llm_enabled: false,
  llm_model: null,
  llm_api_base: null,
  llm_api_key_set: false,
  smtp_enabled: false,
  smtp_host: null,
  smtp_port: 587,
  smtp_security: "starttls" as const,
  smtp_username: null,
  smtp_password_set: false,
  smtp_from: null,
  smtp_from_name: null,
  app_url: null,
  updated_at: "2026-09-04T00:00:00Z",
  crowdsec_hub_auto_update: true,
  crowdsec_hub_update_frequency: "daily" as const,
  crowdsec_hub_update_weekday: 6,
  crowdsec_hub_update_hour_utc: 3,
  crowdsec_capi_enabled: false,
};
const EMPTY = { hub: null, capi: null, reload_configured: true, running: { hub: false, capi: false } };

beforeEach(() => {
  vi.spyOn(instanceSettings, "get").mockResolvedValue(SETTINGS);
  vi.spyOn(crowdsec, "maintenance").mockResolvedValue(EMPTY);
  vi.spyOn(toast, "success").mockImplementation(() => "" as never);
  vi.spyOn(toast, "error").mockImplementation(() => "" as never);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("UpdatesTab schedule", () => {
  it("renders the schedule and disables Save until something changes", async () => {
    const user = userEvent.setup();
    render(<UpdatesTab />);
    const save = await screen.findByRole("button", { name: /save schedule/i });
    expect(save).toBeDisabled();
    await user.click(screen.getByRole("switch", { name: /update detection rules automatically/i }));
    expect(save).toBeEnabled();
  });

  it("saves the schedule in UTC", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(instanceSettings, "updateCrowdSecHub").mockResolvedValue(SETTINGS);
    render(<UpdatesTab />);
    await screen.findByRole("button", { name: /save schedule/i });
    await user.click(screen.getByRole("switch", { name: /update detection rules automatically/i }));
    await user.click(screen.getByRole("button", { name: /save schedule/i }));
    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update.mock.calls[0][0]).toMatchObject({ auto_update: false, frequency: "daily", hour_utc: 3 });
  });
});

describe("UpdatesTab update now", () => {
  it("confirms with the fail-closed sentence, then queues", async () => {
    const user = userEvent.setup();
    const now = vi.spyOn(crowdsec, "hubUpdateNow").mockResolvedValue({ queued: true });
    render(<UpdatesTab />);
    await user.click(await screen.findByRole("button", { name: /update now/i }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(/deny traffic for a few seconds/i);
    await user.click(within(dialog).getByRole("button", { name: /^update now$/i }));
    await waitFor(() => expect(now).toHaveBeenCalled());
  });

  it("is disabled while a run is in progress", async () => {
    vi.mocked(crowdsec.maintenance).mockResolvedValue({ ...EMPTY, running: { hub: true, capi: false } });
    render(<UpdatesTab />);
    expect(await screen.findByRole("button", { name: /update now/i })).toBeDisabled();
    expect(screen.getByText(/running/i)).toBeInTheDocument();
  });

  it("shows the newer-agent note", async () => {
    vi.mocked(crowdsec.maintenance).mockResolvedValue({
      ...EMPTY,
      hub: {
        kind: "hub_update",
        started_at: "2026-09-04T03:05:00Z",
        finished_at: "2026-09-04T03:06:00Z",
        ok: true,
        error: null,
        trigger: "scheduled",
        restarted: false,
        detail: { updated: [], agent_version: "v1.6.4", latest_agent_version: "v1.8.0" },
      },
    });
    render(<UpdatesTab />);
    expect(await screen.findByText(/v1\.8\.0 is available/i)).toBeInTheDocument();
  });
});

describe("UpdatesTab blocklist", () => {
  it("confirms enabling with the registration sentence, then saves", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(instanceSettings, "updateCrowdSecCapi").mockResolvedValue({ ...SETTINGS, crowdsec_capi_enabled: true });
    render(<UpdatesTab />);
    await user.click(await screen.findByRole("switch", { name: /community blocklist/i }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(/registers this instance/i);
    await user.click(within(dialog).getByRole("button", { name: /turn on/i }));
    await waitFor(() => expect(update).toHaveBeenCalledWith({ enabled: true }));
  });

  it("shows a failed apply with retry", async () => {
    const user = userEvent.setup();
    vi.mocked(instanceSettings.get).mockResolvedValue({ ...SETTINGS, crowdsec_capi_enabled: true });
    vi.mocked(crowdsec.maintenance).mockResolvedValue({
      ...EMPTY,
      capi: {
        kind: "capi_apply",
        started_at: "2026-09-04T03:05:00Z",
        finished_at: "2026-09-04T03:06:00Z",
        ok: false,
        error: "Registering with CrowdSec's central API failed: no route to host",
        trigger: "manual",
        restarted: false,
        detail: { enabled: false },
      },
    });
    const update = vi.spyOn(instanceSettings, "updateCrowdSecCapi").mockResolvedValue(SETTINGS);
    render(<UpdatesTab />);
    expect(await screen.findByText(/no route to host/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /retry/i }));
    await waitFor(() => expect(update).toHaveBeenCalledWith({ enabled: true }));
  });

  it("explains itself when reloads are not configured", async () => {
    vi.mocked(crowdsec.maintenance).mockResolvedValue({ ...EMPTY, reload_configured: false });
    render(<UpdatesTab />);
    expect(await screen.findByRole("switch", { name: /community blocklist/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /update now/i })).toBeDisabled();
    expect(screen.getAllByText(/CROWDSEC_CONTROL_NODE_ID/).length).toBeGreaterThan(0);
  });

  it("surfaces a 409 from Update now", async () => {
    const user = userEvent.setup();
    vi.spyOn(crowdsec, "hubUpdateNow").mockRejectedValue(
      new ApiError(409, "Conflict", { detail: "An update is already running." }),
    );
    render(<UpdatesTab />);
    await user.click(await screen.findByRole("button", { name: /update now/i }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /^update now$/i }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("An update is already running."));
  });
});
```

- [ ] **Step 3: Run them to verify they fail**

```bash
cd frontend && npx vitest run src/components/security/updates-lib.test.ts src/components/security/updates-tab.test.tsx
```
Expected: FAIL — modules not found.

- [ ] **Step 4: `updates-lib.ts`**

```ts
import type { CrowdSecJobRun } from "@/lib/api";

export const WEEKDAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
] as const;

/** The browser's offset from UTC in whole hours at `now` (DST-aware). */
function offsetHours(now: Date): number {
  return Math.round(-now.getTimezoneOffset() / 60);
}

export function utcHourToLocal(hourUtc: number, now: Date = new Date()): number {
  return (((hourUtc + offsetHours(now)) % 24) + 24) % 24;
}

export function localHourToUtc(hourLocal: number, now: Date = new Date()): number {
  return (((hourLocal - offsetHours(now)) % 24) + 24) % 24;
}

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** One sentence for the Hub card's status line. */
export function describeHubRun(run: CrowdSecJobRun | null): string {
  if (!run) return "Never run.";
  if (!run.finished_at) return `Running since ${when(run.started_at)}…`;
  if (!run.ok) return `Last run ${when(run.started_at)} failed: ${run.error ?? "unknown error"}`;
  const updated = Array.isArray(run.detail.updated) ? (run.detail.updated as string[]) : [];
  if (updated.length === 0) return `Last run ${when(run.started_at)}: no changes.`;
  const n = updated.length;
  return `Last run ${when(run.started_at)}: ${n} item${n === 1 ? "" : "s"} updated${
    run.restarted ? ", CrowdSec restarted" : ""
  }.`;
}

/** The Blocklist card's state: desired vs achieved. */
export function describeCapiRun(
  desired: boolean,
  run: CrowdSecJobRun | null,
  running: boolean,
): { label: string; failed: boolean } {
  if (running || (run && !run.finished_at)) {
    return { label: desired ? "Turning on…" : "Turning off…", failed: false };
  }
  if (run && !run.ok) {
    return {
      label: `Failed: ${run.error ?? "unknown error"} — the previous configuration was restored.`,
      failed: true,
    };
  }
  const achieved = run ? run.detail.enabled === true : false;
  if (desired === achieved) return { label: desired ? "On" : "Off", failed: false };
  // Desired but never applied (e.g. saved while reloads were unconfigured).
  return { label: desired ? "Off — not applied yet" : "On — not applied yet", failed: false };
}
```

- [ ] **Step 5: The two cards and the tab**

`hub-updates-card.tsx`:

```tsx
"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { instanceSettings, type CrowdSecJobRun, type HubUpdateFrequency, type InstanceSettings } from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { ConfirmDeleteDialog } from "@/components/proxy-hosts/confirm-delete-dialog";
import { describeHubRun, localHourToUtc, utcHourToLocal, WEEKDAYS } from "@/components/security/updates-lib";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

const HOURS = Array.from({ length: 24 }, (_, h) => h);

function pad(h: number): string {
  return `${String(h).padStart(2, "0")}:00`;
}

export function HubUpdatesCard({
  settings,
  run,
  running,
  reloadConfigured,
  onSaved,
  onQueued,
  onUpdateNow,
}: {
  settings: InstanceSettings;
  run: CrowdSecJobRun | null;
  running: boolean;
  reloadConfigured: boolean;
  onSaved: (next: InstanceSettings) => void;
  onQueued: () => void;
  onUpdateNow: () => Promise<void>;
}) {
  const [auto, setAuto] = useState(settings.crowdsec_hub_auto_update);
  const [frequency, setFrequency] = useState<HubUpdateFrequency>(settings.crowdsec_hub_update_frequency);
  const [weekday, setWeekday] = useState(settings.crowdsec_hub_update_weekday);
  const [hourLocal, setHourLocal] = useState(utcHourToLocal(settings.crowdsec_hub_update_hour_utc));
  const [saving, setSaving] = useState(false);
  const [confirm, setConfirm] = useState(false);

  const hourUtc = localHourToUtc(hourLocal);
  const dirty =
    auto !== settings.crowdsec_hub_auto_update ||
    frequency !== settings.crowdsec_hub_update_frequency ||
    weekday !== settings.crowdsec_hub_update_weekday ||
    hourUtc !== settings.crowdsec_hub_update_hour_utc;

  async function save() {
    setSaving(true);
    try {
      const next = await instanceSettings.updateCrowdSecHub({
        auto_update: auto,
        frequency,
        weekday,
        hour_utc: hourUtc,
      });
      toast.success("Schedule saved");
      onSaved(next);
    } catch (err) {
      toast.error(describeError(err).message);
    } finally {
      setSaving(false);
    }
  }

  const agent = typeof run?.detail.agent_version === "string" ? run.detail.agent_version : null;
  const latest = typeof run?.detail.latest_agent_version === "string" ? run.detail.latest_agent_version : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <RefreshCw className="size-4" /> Detection rules
        </CardTitle>
        <CardDescription>
          CrowdSec&apos;s parsers, scenarios and AppSec rules come from its hub and only refresh
          when the container starts. This keeps them current. If a refresh changes anything,
          CrowdSec restarts and protected hosts deny traffic for a few seconds.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <label className="flex items-center justify-between gap-3 text-sm">
          <span>Update detection rules automatically</span>
          <Switch
            checked={auto}
            onCheckedChange={(v) => setAuto(Boolean(v))}
            aria-label="Update detection rules automatically"
            disabled={saving}
          />
        </label>
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="space-y-1.5">
            <Label htmlFor="hub-frequency">Frequency</Label>
            <Select value={frequency} onValueChange={(v) => setFrequency(v as HubUpdateFrequency)}>
              <SelectTrigger id="hub-frequency" disabled={!auto || saving}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="daily">Daily</SelectItem>
                <SelectItem value="weekly">Weekly</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {frequency === "weekly" ? (
            <div className="space-y-1.5">
              <Label htmlFor="hub-weekday">Day</Label>
              <Select value={String(weekday)} onValueChange={(v) => setWeekday(Number(v))}>
                <SelectTrigger id="hub-weekday" disabled={!auto || saving}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {WEEKDAYS.map((name, i) => (
                    <SelectItem key={name} value={String(i)}>
                      {name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : null}
          <div className="space-y-1.5">
            <Label htmlFor="hub-hour">Time</Label>
            <Select value={String(hourLocal)} onValueChange={(v) => setHourLocal(Number(v))}>
              <SelectTrigger id="hub-hour" disabled={!auto || saving}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {HOURS.map((h) => (
                  <SelectItem key={h} value={String(h)}>
                    {pad(h)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-muted-foreground text-xs">Your time. {pad(hourUtc)} UTC.</p>
          </div>
        </div>

        <div className="space-y-1 text-sm">
          <p>{describeHubRun(run)}</p>
          {agent ? (
            <p className="text-muted-foreground text-xs">
              CrowdSec {agent}
              {latest && latest !== agent
                ? ` — ${latest} is available; rules that need it are skipped until the image is updated.`
                : ""}
            </p>
          ) : null}
          {!reloadConfigured ? (
            <p className="text-destructive text-xs">
              CrowdSec reloads are not configured: set CROWDSEC_CONTROL_NODE_ID to the node whose
              worker has the docker socket.
            </p>
          ) : null}
        </div>
      </CardContent>
      <CardFooter className="justify-between gap-2">
        <Button
          variant="outline"
          onClick={() => setConfirm(true)}
          disabled={running || !reloadConfigured}
        >
          <RefreshCw /> {running ? "Running…" : "Update now"}
        </Button>
        <Button onClick={() => void save()} disabled={!dirty || saving}>
          {saving ? "Saving…" : "Save schedule"}
        </Button>
      </CardFooter>

      <ConfirmDeleteDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="Update now"
        description="This checks the CrowdSec hub for newer rules. If anything changed, CrowdSec restarts and protected hosts deny traffic for a few seconds."
        confirmLabel="Update now"
        onConfirm={onUpdateNow}
        onDeleted={onQueued}
      />
    </Card>
  );
}
```

`ConfirmDeleteDialog` needs an optional `confirmLabel` prop (default
"Delete") — add it: `confirmLabel?: string` in the props, rendered on the
destructive button. Its existing tests keep passing because the default is
unchanged.

`blocklist-card.tsx`:

```tsx
"use client";

import { useState } from "react";
import { Globe } from "lucide-react";
import { toast } from "sonner";

import { instanceSettings, type CrowdSecJobRun } from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { ConfirmDeleteDialog } from "@/components/proxy-hosts/confirm-delete-dialog";
import { describeCapiRun } from "@/components/security/updates-lib";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";

export function BlocklistCard({
  desired,
  run,
  running,
  reloadConfigured,
  onChanged,
}: {
  desired: boolean;
  run: CrowdSecJobRun | null;
  running: boolean;
  reloadConfigured: boolean;
  onChanged: () => void;
}) {
  const [pending, setPending] = useState<boolean | null>(null);
  const state = describeCapiRun(desired, run, running);

  async function apply(enabled: boolean) {
    await instanceSettings.updateCrowdSecCapi({ enabled });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Globe className="size-4" /> Community blocklist
        </CardTitle>
        <CardDescription>
          CrowdSec&apos;s shared threat intelligence: addresses reported by other CrowdSec users are
          blocked here too, and this instance&apos;s alerts are shared back. Once on, it refreshes
          itself every two hours.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <label className="flex items-center justify-between gap-3 text-sm">
          <span>Use the CrowdSec community blocklist</span>
          <Switch
            checked={desired}
            onCheckedChange={(v) => setPending(Boolean(v))}
            aria-label="Use the CrowdSec community blocklist"
            disabled={running || !reloadConfigured}
          />
        </label>
        <p className={state.failed ? "text-destructive text-sm" : "text-muted-foreground text-sm"}>
          {state.label}
        </p>
        {state.failed ? (
          <div>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                apply(desired)
                  .then(() => {
                    toast.success("Retrying…");
                    onChanged();
                  })
                  .catch((err: unknown) => toast.error(describeError(err).message));
              }}
            >
              Retry
            </Button>
          </div>
        ) : null}
        {!reloadConfigured ? (
          <p className="text-destructive text-xs">
            CrowdSec reloads are not configured: set CROWDSEC_CONTROL_NODE_ID to the node whose
            worker has the docker socket.
          </p>
        ) : null}
      </CardContent>

      <ConfirmDeleteDialog
        open={pending !== null}
        onOpenChange={(open) => {
          if (!open) setPending(null);
        }}
        title={pending ? "Turn on the community blocklist?" : "Turn off the community blocklist?"}
        description={
          pending
            ? "CrowdSec restarts and protected hosts deny traffic for a few seconds. This registers this instance with CrowdSec's central service."
            : "CrowdSec restarts and protected hosts deny traffic for a few seconds."
        }
        confirmLabel={pending ? "Turn on" : "Turn off"}
        onConfirm={() => apply(pending === true)}
        onDeleted={() => {
          setPending(null);
          onChanged();
        }}
      />
    </Card>
  );
}
```

`updates-tab.tsx`:

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { crowdsec, instanceSettings, type CrowdSecMaintenance, type InstanceSettings } from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { BlocklistCard } from "@/components/security/blocklist-card";
import { HubUpdatesCard } from "@/components/security/hub-updates-card";
import { Skeleton } from "@/components/ui/skeleton";

const POLL_MS = 5000;

/** Loads the settings and both job records; polls while a job is running. */
export function UpdatesTab() {
  const [settings, setSettings] = useState<InstanceSettings | null>(null);
  const [maint, setMaint] = useState<CrowdSecMaintenance | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, m] = await Promise.all([instanceSettings.get(), crowdsec.maintenance()]);
      setSettings(s);
      setMaint(m);
      setError(null);
    } catch (err) {
      setError(describeError(err).message);
    }
  }, []);

  useEffect(() => {
    let active = true;
    void (async () => {
      if (active) await load();
    })();
    return () => {
      active = false;
    };
  }, [load]);

  const busy = Boolean(maint && (maint.running.hub || maint.running.capi || (maint.hub && !maint.hub.finished_at) || (maint.capi && !maint.capi.finished_at)));
  useEffect(() => {
    if (!busy) return;
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [busy, load]);

  if (error) {
    return (
      <p role="alert" className="text-destructive text-sm">
        {error}
      </p>
    );
  }
  if (!settings || !maint) return <Skeleton className="h-40 w-full" />;

  return (
    <div className="grid gap-4">
      <HubUpdatesCard
        key={settings.updated_at}
        settings={settings}
        run={maint.hub}
        running={maint.running.hub}
        reloadConfigured={maint.reload_configured}
        onSaved={setSettings}
        onQueued={() => void load()}
        onUpdateNow={async () => {
          try {
            await crowdsec.hubUpdateNow();
            toast.success("Update queued");
          } catch (err) {
            toast.error(describeError(err).message);
            throw err;
          }
        }}
      />
      <BlocklistCard
        desired={settings.crowdsec_capi_enabled}
        run={maint.capi}
        running={maint.running.capi}
        reloadConfigured={maint.reload_configured}
        onChanged={() => void load()}
      />
    </div>
  );
}
```

Read `ConfirmDeleteDialog` before wiring `onUpdateNow`: it catches an
`ApiError` from `onConfirm` and toasts it, then does not call `onDeleted`.
If it already toasts, drop the `toast.error` in `onUpdateNow` and let the
dialog do it, so the 409 test sees exactly one toast with the backend's
text.

In `security-view.tsx`: import `RefreshCw` and `UpdatesTab`; add the tab
after Whitelists:

```tsx
          <TabsTab value="updates">
            <RefreshCw /> Updates
          </TabsTab>
```
```tsx
        <TabsPanel value="updates" className="space-y-3">
          <UpdatesTab />
        </TabsPanel>
```

- [ ] **Step 6: Run, typecheck, lint, full suite; commit**

```bash
cd frontend && npx prettier --write --print-width 100 src/components/security src/lib/api src/components/proxy-hosts/confirm-delete-dialog.tsx
npx vitest run src/components/security src/components/proxy-hosts && npx tsc --noEmit && npm run lint && npx vitest run
```
Commit separately:
```bash
git add frontend/src
git commit -m "feat(security): the Updates tab — hub schedule, Update now, community blocklist

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Compose and docs

**Files:**
- Modify: `docker-compose.yml`, `docker-compose.dev.yml`, `docker-compose.ha.yml`, `docs/crowdsec.md`
- Test: `backend/tests/test_compose_crowdsec_whitelists.py` (append)

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_compose_crowdsec_whitelists.py` already has
`_services(compose_file) -> dict` (parses the YAML from the repo root and
returns `services`) and `_mount_targets`. Append:

```python
ALL_COMPOSE = ["docker-compose.yml", "docker-compose.dev.yml", "docker-compose.ha.yml"]
CONFIG_LOCAL_TARGET = "/etc/crowdsec/config.yaml.local"


def _sources_for(mounts: list, target: str) -> list[str]:
    """The source (host path, volume, or volume+subpath) of every mount at ``target``."""
    out = []
    for m in mounts:
        if isinstance(m, str):
            src, _, rest = m.partition(":")
            if rest.split(":")[0] == target:
                out.append(src)
        elif m.get("target") == target:
            out.append(f"{m.get('source')}:{(m.get('volume') or {}).get('subpath', '')}")
    return out


@pytest.mark.parametrize("compose_file", ALL_COMPOSE)
def test_crowdsec_mounts_the_app_owned_config_local(compose_file: str) -> None:
    services = _services(compose_file)
    sources = _sources_for(services["crowdsec"]["volumes"], CONFIG_LOCAL_TARGET)
    assert len(sources) == 1, sources
    # The app-owned file under the data path, never the repo template.
    assert "infra/crowdsec" not in sources[0]
    assert "crowdsec/config.yaml.local" in sources[0]


@pytest.mark.parametrize("compose_file", ALL_COMPOSE)
def test_data_init_seeds_config_local(compose_file: str) -> None:
    services = _services(compose_file)
    command = " ".join(services["data-init"]["command"])
    assert "config.yaml.local" in command
    # Seeded from the repo template, mounted read-only for that purpose.
    assert any(
        v.endswith("/seed/config.yaml.local:ro") for v in services["data-init"]["volumes"]
    )


@pytest.mark.parametrize("compose_file", ALL_COMPOSE)
def test_crowdsec_uses_wal(compose_file: str) -> None:
    services = _services(compose_file)
    assert services["crowdsec"]["environment"]["USE_WAL"] == "true"


def test_dev_worker_can_reach_the_socket_and_the_data_path() -> None:
    services = _services("docker-compose.dev.yml")
    volumes = services["worker"]["volumes"]
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in volumes
    assert "app_data:/data" in volumes
```

- [ ] **Step 2: Run them to verify they fail**

```bash
docker exec megoopm-test python -m pytest tests/test_compose_crowdsec_whitelists.py -p no:cacheprovider -p no:warnings
```
Expected: the new tests FAIL; the existing ones still pass.

- [ ] **Step 3: Compose**

Three files, one rule: `config.yaml.local` is a single-file mount out of the
data path, seeded by `data-init` from `./infra/crowdsec/config.yaml.local`
(mounted read-only at `/seed/config.yaml.local` on `data-init` only).

**`docker-compose.yml` (production, single node)** — already has `app_data`,
the socket on the worker, and the whitelist as a `subpath` mount:

- `data-init.command`: before `&& chown -R`, add
  `&& { [ -f /data/crowdsec/config.yaml.local ] || cp /seed/config.yaml.local /data/crowdsec/config.yaml.local; }`.
  `data-init.volumes`: add `- ./infra/crowdsec/config.yaml.local:/seed/config.yaml.local:ro`.
- `crowdsec.volumes`: replace the `./infra/crowdsec/config.yaml.local:…:ro`
  line with a second subpath mount, same shape as the whitelist one:

  ```yaml
      # The app-owned config override (Security → Updates rewrites it for the
      # community blocklist). Seeded by data-init from the repo template.
      - type: volume
        source: app_data
        target: /etc/crowdsec/config.yaml.local
        read_only: true
        volume:
          subpath: crowdsec/config.yaml.local
  ```
- `crowdsec.environment`: add `USE_WAL: "true"` with a comment: the
  community blocklist inserts ~15k rows and CrowdSec warns LAPI may stall
  without WAL.

**`docker-compose.dev.yml`** — mirrors production instead of the repo-file
mounts it has today:

- Add a named volume `app_data:` to the top-level `volumes:`.
- `data-init`: command becomes the production one (whitelist + config seed);
  volumes gain `- app_data:/data` and the `/seed` mount. Keep the
  `nginx_confd`/`nginx_certs` mounts as they are — nested mount points are
  fine.
- `worker.volumes`: add `- app_data:/data` and
  `- /var/run/docker.sock:/var/run/docker.sock:ro` (copy the HA file's
  comment on why the socket is on the worker only).
- `crowdsec`: add `depends_on: data-init: condition: service_completed_successfully`
  (with the production file's comment), `USE_WAL: "true"`, and replace the
  `./infra/crowdsec/config.yaml.local` line with the two subpath mounts from
  production (whitelist and config override). Dev has had no whitelist
  mount at all; this gives it one.

**`docker-compose.ha.yml`** — host-path binds out of `${SHARED_DATA_PATH}`:

- `data-init.command`: same seed clause before `&& chown -R`; volumes gain
  the `/seed` mount.
- `crowdsec.volumes`: replace the repo-file line with
  `- ${SHARED_DATA_PATH:?}/crowdsec/config.yaml.local:/etc/crowdsec/config.yaml.local:ro`.
- `crowdsec.environment`: add `USE_WAL: "true"`.

Then the compose tests, plus a parse check of all three:

```bash
docker exec megoopm-test python -m pytest tests/test_compose_crowdsec_whitelists.py tests/test_compose_config.py -p no:cacheprovider -p no:warnings
```
(`test_compose_config.py` runs `docker compose config` on every file with
the example env files and skips when compose is unavailable; run the same
three commands on the host if it skipped.)

- [ ] **Step 4: Docs**

In `docs/crowdsec.md`: change the CAPI row in the moving-parts table to
"off by default; switch it on in Security → Updates (registers with CAPI,
restarts CrowdSec)"; add a section **Updates tab (hub refresh + community
blocklist)** after the Whitelists section covering: what the hourly tick
does and the defaults; that a refresh with changes restarts CrowdSec; the
tarball rollback and its limits; that `config.yaml.local` is app-owned and
seeded by data-init, with the "keep `_BASE` and the infra file in step"
note; the `CROWDSEC_CONTROL_NODE_ID` requirement under HA; how to verify on
a live stack (`cscli hub list`, `cscli capi status`, container uptime after
an idle run does not change). Update the Configuration section with
`CROWDSEC_CONFIG_LOCAL_PATH`.

- [ ] **Step 5: Run, commit, tear down**

```bash
docker exec megoopm-test python -m pytest tests/test_compose_crowdsec_whitelists.py -p no:cacheprovider -p no:warnings
```
Commit separately:
```bash
git add docker-compose.yml docker-compose.dev.yml docker-compose.ha.yml docs/crowdsec.md backend/tests/test_compose_crowdsec_whitelists.py
git commit -m "chore(compose): app-owned config.yaml.local in all three stacks, dev gets the socket, USE_WAL

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
export MSYS_NO_PATHCONV=1; docker rm -f megoopm-test megoopm-testdb && docker network rm megoopm-testnet
```

---

## Manual verification

- [ ] Rebuild and start the dev stack. `data-init` seeds
      `config.yaml.local` into the `app_data` volume; CrowdSec starts; the
      worker can reach the socket
      (`docker compose -f docker-compose.dev.yml exec worker ls -l /var/run/docker.sock`).
- [ ] Security → Updates shows the schedule with today's defaults, "Never
      run", and the blocklist Off.
- [ ] Update now: confirm; within a minute the status line reads either
      "no changes" or "N items updated, CrowdSec restarted". `docker compose
      exec crowdsec cscli hub list` agrees. On an idle run the container's
      uptime does not change.
- [ ] Change the hour to the next hour in local time, save, wait for :05
      past; the run is recorded with trigger "scheduled".
- [ ] Turn the blocklist on: confirm; the state passes through "Turning
      on…" to "On"; `cscli capi status` succeeds; `cscli decisions list -o
      json | head` shows community entries within a few minutes.
- [ ] Turn it off; "Off"; `cscli capi status` says no configuration.
- [ ] Break it deliberately: stop outbound internet for the container (or
      set a bogus DNS) and turn it on; the state shows the registration
      error and the previous configuration; CrowdSec is still up.
