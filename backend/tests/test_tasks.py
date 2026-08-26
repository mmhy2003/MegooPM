"""Tests for the Celery async-task infrastructure.

These run with Celery in eager mode (configured in ``conftest.py``): tasks
execute inline and their results are stored in an in-memory backend, so the full
enqueue -> execute -> status-lookup path is exercised without a broker/worker.
"""

from __future__ import annotations

from app.core.celery_app import celery_app
from app.services.tasks import get_task_status
from app.tasks.sample import add, heartbeat
from httpx import AsyncClient


def test_celery_runs_eager_in_tests() -> None:
    assert celery_app.conf.task_always_eager is True


def test_sample_task_executes_and_returns_result() -> None:
    result = add.delay(2, 3)
    assert result.get(timeout=5) == 5
    assert result.status == "SUCCESS"


def test_heartbeat_task_runs() -> None:
    assert heartbeat.delay().get(timeout=5) == "ok"


def test_beat_schedule_registers_heartbeat() -> None:
    schedule = celery_app.conf.beat_schedule
    assert "heartbeat-every-5-minutes" in schedule
    assert schedule["heartbeat-every-5-minutes"]["task"] == "app.tasks.sample.heartbeat"


def test_status_helper_returns_result_for_completed_task() -> None:
    result = add.delay(4, 5)
    result.get(timeout=5)

    status = get_task_status(result.id)
    assert status.ready is True
    assert status.status == "SUCCESS"
    assert status.result == 9
    assert status.error is None


def test_status_helper_pending_for_unknown_id() -> None:
    status = get_task_status("does-not-exist")
    assert status.status == "PENDING"
    assert status.ready is False


async def test_enqueue_and_lookup_via_api(client: AsyncClient) -> None:
    enqueue = await client.post("/api/v1/tasks/sample", json={"x": 7, "y": 8})
    assert enqueue.status_code == 202
    task_id = enqueue.json()["task_id"]
    assert task_id

    lookup = await client.get(f"/api/v1/tasks/{task_id}")
    assert lookup.status_code == 200
    body = lookup.json()
    assert body["status"] == "SUCCESS"
    assert body["ready"] is True
    assert body["result"] == 15
