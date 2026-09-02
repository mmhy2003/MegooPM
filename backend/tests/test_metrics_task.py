"""The per-node scrape task.

The HTTP call is stubbed. What is worth testing is that a failed scrape writes
*nothing*: the previous row then ages out of the totals on the staleness rule,
which reports the node as unknown. Writing zeros instead would report a node
whose nginx is down as an idle one.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest

pytestmark = pytest.mark.asyncio

BODY = "Active connections: 3 \nserver accepts handled requests\n 10 10 40 \n"


def _null_session_factory():
    """A factory whose session is never used, because record_sample is stubbed."""

    @asynccontextmanager
    async def factory():
        yield object()

    return factory


async def test_a_successful_scrape_records_a_sample(monkeypatch) -> None:
    from app.tasks import metrics

    recorded: dict[str, object] = {}

    async def fake_fetch(url: str) -> str:
        return BODY

    async def fake_record(session, node_id, sample, *, now):
        recorded["node_id"] = node_id
        recorded["active"] = sample.active
        recorded["requests"] = sample.requests

    monkeypatch.setattr(metrics, "_fetch", fake_fetch)
    monkeypatch.setattr(metrics, "record_sample", fake_record)

    await metrics._scrape_async(session_factory=_null_session_factory())

    assert recorded["active"] == 3
    assert recorded["requests"] == 40
    assert recorded["node_id"]  # whichever node this process is


async def test_an_unreachable_nginx_records_nothing(monkeypatch) -> None:
    """Leaving the previous row is right: it goes stale on its own and drops out
    of the totals."""
    from app.tasks import metrics

    async def boom(url: str) -> str:
        raise OSError("connection refused")

    called = False

    async def fake_record(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(metrics, "_fetch", boom)
    monkeypatch.setattr(metrics, "record_sample", fake_record)

    await metrics._scrape_async(session_factory=_null_session_factory())

    assert called is False


async def test_an_http_error_records_nothing(monkeypatch) -> None:
    from app.tasks import metrics

    async def bad_status(url: str) -> str:
        raise httpx.HTTPError("502")

    called = False

    async def fake_record(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(metrics, "_fetch", bad_status)
    monkeypatch.setattr(metrics, "record_sample", fake_record)

    await metrics._scrape_async(session_factory=_null_session_factory())

    assert called is False


async def test_a_garbage_body_records_nothing(monkeypatch) -> None:
    """An error page parsed as numbers would report noise as a connection
    count, which is worse than reporting nothing."""
    from app.tasks import metrics

    async def html(url: str) -> str:
        return "<html>404</html>"

    called = False

    async def fake_record(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(metrics, "_fetch", html)
    monkeypatch.setattr(metrics, "record_sample", fake_record)

    await metrics._scrape_async(session_factory=_null_session_factory())

    assert called is False


def test_the_scrape_is_routed_to_this_node_in_ha() -> None:
    """A tick executed on another node would scrape THAT node's nginx and upsert
    its row, leaving this node unmeasured and the other double-counted.

    Calls the HA wiring directly rather than skipping when HA is off, because a
    skipped test would hide the one constraint this task most depends on.
    """
    from app.core.celery_app import _configure_ha, node_queue
    from app.core.config import settings
    from celery import Celery

    app = Celery("probe")
    app.conf.beat_schedule = {}
    _configure_ha(app)

    routes = app.conf.task_routes or {}
    assert routes["app.tasks.metrics.scrape_local_nginx"] == {
        "queue": node_queue(settings.effective_node_id)
    }


def test_the_scrape_is_on_the_beat_schedule() -> None:
    from app.core.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule or {}
    tasks = {entry["task"] for entry in schedule.values()}
    assert "app.tasks.metrics.scrape_local_nginx" in tasks
