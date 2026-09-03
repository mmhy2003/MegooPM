"""The tasks: settings in, callables faked, run record out."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from app.models.crowdsec_job_run import CrowdSecJobRun
from app.models.enums import CrowdSecJobKind, HubUpdateFrequency
from app.models.instance_settings import InstanceSettings
from app.services.crowdsec import capi, hub
from app.services.crowdsec.job_run import read_job_run
from app.services.crowdsec.reload import ExecResult
from app.tasks import crowdsec as tasks
from sqlalchemy import create_engine, insert
from sqlalchemy.pool import StaticPool


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


class _NoDispose:
    """The tasks dispose their engine when done; an in-memory SQLite database
    dies with its connection, so the test's engine must survive that."""

    def __init__(self, eng) -> None:
        self._eng = eng

    def begin(self):
        return self._eng.begin()

    def dispose(self) -> None:
        pass


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Iterator:
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    for table in (InstanceSettings.__table__, CrowdSecJobRun.__table__):
        table.create(eng)
    with eng.begin() as conn:
        conn.execute(
            insert(InstanceSettings.__table__).values(
                id=1,
                default_site_mode="not_found",
                crowdsec_ban_mode="megoopm",
                crowdsec_hub_auto_update=True,
                crowdsec_hub_update_frequency="daily",
                crowdsec_hub_update_weekday=6,
                crowdsec_hub_update_hour_utc=3,
                crowdsec_capi_enabled=False,
            )
        )
    monkeypatch.setattr(tasks, "sync_engine", lambda: _NoDispose(eng))
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
        if argv == capi.CMD_STATUS:
            return ExecResult(0, "You can successfully interact with Central API (CAPI)")
        return ExecResult(0, "")

    def fake_restart():
        state["restarts"] += 1

    monkeypatch.setattr(tasks, "_container_exec", fake_exec)
    monkeypatch.setattr(tasks, "_container_restart", fake_restart)
    monkeypatch.setattr(tasks, "_wait_for_lapi", lambda: True)
    monkeypatch.setattr(tasks, "_lock_client", lambda: FakeRedis())
    monkeypatch.setattr(
        tasks.settings, "crowdsec_config_local_path", str(tmp_path / "config.yaml.local")
    )
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
