"""The cert-renewal beat sweep must enqueue once per period, cluster-wide.

``beat`` runs on every node (each node schedules its own nginx reconcile), so the
daily renewal sweep is emitted N times. ``leader_lock`` alone does not make that
safe: it excludes concurrent runs, and these runs are sequential.
"""

from __future__ import annotations

from pathlib import Path

import app.tasks.certs as certs_task
import pytest
from app.core.config import settings
from app.models.cluster_state import ClusterSweep
from sqlalchemy import create_engine


@pytest.fixture
def ha_sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """HA mode against a temp SQLite engine, with the enqueue body counted."""
    engine = create_engine(f"sqlite:///{tmp_path / 'sweep.db'}", future=True)
    ClusterSweep.__table__.create(engine)

    monkeypatch.setattr(settings, "ha_enabled", True)
    monkeypatch.setattr(settings, "ha_lock_dir", str(tmp_path / "run"))
    monkeypatch.setattr(settings, "cert_renew_sweep_min_interval_seconds", 3600.0)

    # certs.py imports these lazily from app.services.cluster at call time.
    import app.services.cluster as cluster

    monkeypatch.setattr(cluster, "sync_engine", lambda: engine)

    calls: list[int] = []
    monkeypatch.setattr(
        certs_task, "_enqueue_due_renewals", lambda: (calls.append(1), {"due_count": 0})[1]
    )
    return {"engine": engine, "calls": calls}


def test_first_beat_sweeps(ha_sweep) -> None:
    result = certs_task.renew_due_certificates()
    assert result.get("skipped") is not True
    assert len(ha_sweep["calls"]) == 1


def test_a_second_beat_moments_later_does_not_re_enqueue(ha_sweep) -> None:
    """The regression: N nodes' beats all fire the daily sweep.

    Each duplicate enqueues another ACME order per due certificate, burning Let's
    Encrypt's five-duplicates-per-week limit at N times the intended rate.
    """
    certs_task.renew_due_certificates()
    result = certs_task.renew_due_certificates()

    assert result["skipped"] is True
    assert len(ha_sweep["calls"]) == 1, "the sweep body must run once per period"


def test_non_ha_is_unguarded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A single host has one beat and no shared DB — keep the original path."""
    monkeypatch.setattr(settings, "ha_enabled", False)
    calls: list[int] = []
    monkeypatch.setattr(
        certs_task, "_enqueue_due_renewals", lambda: (calls.append(1), {"due_count": 0})[1]
    )
    certs_task.renew_due_certificates()
    certs_task.renew_due_certificates()
    assert len(calls) == 2
