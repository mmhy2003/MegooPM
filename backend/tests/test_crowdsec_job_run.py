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
    finish_job_run(
        conn, CrowdSecJobKind.capi_apply, ok=False, error="boom", restarted=False, detail={}
    )
    start_job_run(conn, CrowdSecJobKind.capi_apply, trigger=CrowdSecJobTrigger.scheduled)
    row = read_job_run(conn, CrowdSecJobKind.capi_apply)
    assert row is not None and row.error is None and row.finished_at is None


def test_kinds_are_independent(conn: Connection) -> None:
    start_job_run(conn, CrowdSecJobKind.hub_update, trigger=CrowdSecJobTrigger.manual)
    assert read_job_run(conn, CrowdSecJobKind.capi_apply) is None
