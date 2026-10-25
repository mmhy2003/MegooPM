"""A worker renders the managed nginx config once, when it is ready.

Nothing else re-renders at startup. After an upgrade that changes the
templates, every host would keep the shape the previous release rendered
until someone changed something: after the Live logs upgrade, only the
default server would stream. The render goes through ``reload_nginx_config``,
the same apply an admin's change does, with its validation, rollback and (in
HA) apply lock.
"""

from __future__ import annotations

import app.tasks.nginx as nginx_task
import pytest
from celery.signals import worker_ready


def test_a_ready_worker_sends_one_render(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict] = []
    monkeypatch.setattr(
        nginx_task.reload_nginx_config, "apply_async", lambda *a, **kw: sent.append(kw)
    )

    worker_ready.send(sender=None)

    assert len(sent) == 1
    # A moment's grace: nginx's reload agent may still be starting with the
    # worker, and an apply it cannot reach would roll back and say so.
    assert sent[0]["countdown"] > 0


def test_a_broker_that_is_down_does_not_stop_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_a, **_kw):
        raise ConnectionError("broker down")

    monkeypatch.setattr(nginx_task.reload_nginx_config, "apply_async", refuse)

    nginx_task.render_nginx_on_worker_start()  # does not raise
