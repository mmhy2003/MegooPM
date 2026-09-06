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
from sqlalchemy import create_engine, insert, select
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


# --- CAPI credential health ----------------------------------------------------


def test_the_check_records_healthy_credentials(engine, fakes, tmp_path) -> None:
    (tmp_path / "config.yaml.local").write_text(
        capi.render_config_local(capi_enabled=True), encoding="utf-8"
    )
    with engine.begin() as conn:
        conn.execute(InstanceSettings.__table__.update().values(crowdsec_capi_enabled=True))

    out = tasks.check_capi_credentials.run()

    assert out == {"enabled": True, "ok": True}
    with engine.begin() as conn:
        row = conn.execute(select(InstanceSettings.__table__)).one()
    assert row.crowdsec_capi_status_ok is True
    assert row.crowdsec_capi_status_detail is None
    assert row.crowdsec_capi_checked_at is not None


def test_the_check_records_a_rejection_with_its_reason(
    engine, fakes, tmp_path, monkeypatch
) -> None:
    """The whole point: this is written down while the container is still up."""
    (tmp_path / "config.yaml.local").write_text(
        capi.render_config_local(capi_enabled=True), encoding="utf-8"
    )
    monkeypatch.setattr(
        tasks, "_container_exec", lambda argv: ExecResult(1, 'msg="API error: Forbidden"')
    )

    out = tasks.check_capi_credentials.run()

    assert out == {"enabled": True, "ok": False}
    with engine.begin() as conn:
        row = conn.execute(select(InstanceSettings.__table__)).one()
    assert row.crowdsec_capi_status_ok is False
    assert "Forbidden" in row.crowdsec_capi_status_detail
    assert "restart" in row.crowdsec_capi_status_detail.lower()


def test_the_check_does_nothing_when_the_blocklist_is_off(engine, fakes, tmp_path) -> None:
    (tmp_path / "config.yaml.local").write_text(
        capi.render_config_local(capi_enabled=False), encoding="utf-8"
    )

    out = tasks.check_capi_credentials.run()

    assert out == {"enabled": False, "ok": None}
    with engine.begin() as conn:
        row = conn.execute(select(InstanceSettings.__table__)).one()
    # Not "healthy" and not "rejected": there is nothing to authenticate.
    assert row.crowdsec_capi_status_ok is None


def test_re_registering_records_the_outcome(engine, fakes, tmp_path) -> None:
    (tmp_path / "config.yaml.local").write_text(
        capi.render_config_local(capi_enabled=True), encoding="utf-8"
    )

    out = tasks.register_capi.run()

    assert out["ok"] is True and out["restarted"] is True
    with engine.begin() as conn:
        row = read_job_run(conn, CrowdSecJobKind.capi_apply)
        settings_row = conn.execute(select(InstanceSettings.__table__)).one()
    assert row is not None and row.ok
    # `enabled` is what the Updates card reads to decide whether the last CAPI
    # action left the blocklist on; without it the card reports "Off — not
    # applied yet" straight after a successful re-registration.
    assert row.detail == {"enabled": True, "registered": True}
    # The repair refreshes the health it was repairing, so the warning clears
    # without waiting for the next scheduled check.
    assert settings_row.crowdsec_capi_status_ok is True


def test_a_failed_re_registration_is_recorded_as_failed(
    engine, fakes, tmp_path, monkeypatch
) -> None:
    (tmp_path / "config.yaml.local").write_text(
        capi.render_config_local(capi_enabled=True), encoding="utf-8"
    )

    def _exec(argv):
        if argv == capi.CMD_REGISTER:
            return ExecResult(1, 'msg="too many requests"')
        return ExecResult(0, "")

    monkeypatch.setattr(tasks, "_container_exec", _exec)

    out = tasks.register_capi.run()

    assert out["ok"] is False
    with engine.begin() as conn:
        row = read_job_run(conn, CrowdSecJobKind.capi_apply)
    assert row is not None and not row.ok and "too many requests" in row.error
