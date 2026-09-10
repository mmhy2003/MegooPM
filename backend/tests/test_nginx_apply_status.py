"""The last apply's outcome: what it was, where it is kept, who hears about it.

Before this, a config that failed ``nginx -t`` was rolled back and reported only
in a worker log line — truncated, by Celery, just before the part that said
why. The UI said "Proxy host created" and nothing else, while every later change
kept rolling back too.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import app.tasks.nginx as nginx_task
from app.models.cluster_state import CLUSTER_STATE_ROW_ID, ClusterState
from app.services.nginx.apply_status import (
    OUTPUT_LIMIT,
    ApplyOutcome,
    outcome_of,
    record_apply_outcome,
)
from app.services.nginx.controller import CommandResult
from app.services.nginx.engine import ApplyResult
from app.services.nginx.state import BackendSpec, DesiredState, ProxyHostSpec, UpstreamSpec
from sqlalchemy import create_engine, insert, select

NGINX_ERROR = (
    'nginx: [emerg] host not found in upstream "myapp:8080" in '
    "/data/nginx/conf.d/megoopm-proxy-12.conf:31\n"
    "nginx: configuration file /etc/nginx/nginx.conf test failed"
)


def _result(**over) -> ApplyResult:
    base = {
        "changed": True,
        "valid": True,
        "reloaded": True,
        "rolled_back": False,
        "message": "Applied.",
    }
    return ApplyResult(**{**base, **over})


# --- the outcome of a result ---------------------------------------------------


def test_a_failed_test_keeps_nginx_own_words() -> None:
    outcome = outcome_of(
        _result(
            changed=False,
            valid=False,
            reloaded=False,
            rolled_back=True,
            message="Generated configuration failed `nginx -t`; rolled back, nginx untouched.",
            test_output=NGINX_ERROR,
        )
    )

    assert outcome.ok is False
    assert "megoopm-proxy-12.conf" in outcome.output
    assert "nginx -t" in outcome.message


def test_a_failed_reload_reports_the_reload_not_the_passing_test() -> None:
    # nginx -t said "syntax is ok"; that is not why it failed.
    outcome = outcome_of(
        _result(
            changed=False,
            reloaded=False,
            rolled_back=True,
            message="nginx reload failed; rolled back to the previous configuration.",
            test_output="nginx: configuration file ... test is successful",
            reload_output="nginx: [alert] kill(1, 1) failed (3: No such process)",
        )
    )

    assert outcome.ok is False
    assert "kill(1, 1)" in outcome.output
    assert "test is successful" not in outcome.output


def test_an_up_to_date_config_counts_as_healthy() -> None:
    """Nothing changed is a pass, and it must clear an earlier failure.

    After a rollback the disk holds the old good config; deleting the host
    that broke it makes the desired config equal that again, so the next apply
    reports "up to date". That is the moment the problem is fixed.
    """
    outcome = outcome_of(_result(changed=False, reloaded=False, message="already up to date"))

    assert outcome.ok is True
    assert outcome.output == ""


def test_an_enormous_output_is_capped_and_says_so() -> None:
    outcome = outcome_of(
        _result(valid=False, rolled_back=True, reloaded=False, test_output="x" * (OUTPUT_LIMIT * 3))
    )

    assert len(outcome.output) <= OUTPUT_LIMIT + 40
    assert outcome.output.endswith("(truncated)")


# --- where it is kept ----------------------------------------------------------


def test_the_outcome_is_kept_on_the_cluster_state_row() -> None:
    engine = create_engine("sqlite://")
    ClusterState.__table__.create(engine)
    with engine.begin() as conn:
        conn.execute(insert(ClusterState).values(id=CLUSTER_STATE_ROW_ID, config_version=0))

        record_apply_outcome(conn, ApplyOutcome(ok=False, message="failed", output=NGINX_ERROR))

        row = conn.execute(select(ClusterState)).one()
    assert row.last_apply_ok is False
    assert row.last_apply_message == "failed"
    assert "myapp:8080" in row.last_apply_output
    assert row.last_apply_at is not None


# --- the task records, logs and announces it -------------------------------------


class _Controller:
    def __init__(self, test_ok: bool) -> None:
        self.test_ok = test_ok

    def test(self) -> CommandResult:
        output = "syntax is ok" if self.test_ok else NGINX_ERROR
        return CommandResult(ok=self.test_ok, output=output)

    def reload(self) -> CommandResult:
        return CommandResult(ok=True, output="reloaded")


def _state() -> DesiredState:
    pool = UpstreamSpec(id=1, name="p", backends=(BackendSpec(host="10.0.0.1", port=80),))
    return DesiredState(
        proxy_hosts=(ProxyHostSpec(id=12, domain_names=("x.example.com",), upstream_id=1),),
        http_upstreams=(pool,),
    )


def _patch(monkeypatch, tmp_path: Path, *, test_ok: bool) -> tuple[list, list]:
    recorded: list[ApplyOutcome] = []
    announced: list[str] = []
    monkeypatch.setattr(nginx_task, "load_desired_state_sync", _state)
    monkeypatch.setattr(nginx_task, "build_controller", lambda: _Controller(test_ok))
    monkeypatch.setattr(nginx_task.settings, "nginx_confd_dir", str(tmp_path))
    monkeypatch.setattr(nginx_task.settings, "nginx_stream_dir", str(tmp_path / "stream"))
    monkeypatch.setattr(nginx_task, "_record_outcome", recorded.append)
    monkeypatch.setattr(nginx_task, "_announce", announced.append)
    return recorded, announced


def test_a_failed_apply_is_recorded_announced_and_logged_in_full(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    recorded, announced = _patch(monkeypatch, tmp_path, test_ok=False)

    with caplog.at_level(logging.WARNING, logger="app.tasks.nginx"):
        nginx_task.reload_nginx_config.delay().get(timeout=5)

    assert len(recorded) == 1
    assert recorded[0].ok is False
    assert "megoopm-proxy-12.conf" in recorded[0].output
    assert announced == ["config.failed"]
    # Its own log line, so Celery's truncated result repr is no longer the only
    # place the reason appears.
    assert "host not found in upstream" in caplog.text


def test_a_successful_apply_is_recorded_and_announced(monkeypatch, tmp_path: Path) -> None:
    recorded, announced = _patch(monkeypatch, tmp_path, test_ok=True)

    nginx_task.reload_nginx_config.delay().get(timeout=5)

    assert [outcome.ok for outcome in recorded] == [True]
    assert announced == ["config.applied"]


# --- the API reads it ---------------------------------------------------------


async def _seed(session_factory, **values) -> None:
    async with session_factory() as db:
        db.add(ClusterState(id=CLUSTER_STATE_ROW_ID, config_version=0, **values))
        await db.commit()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_the_status_carries_nginx_own_words(db_client, admin_token, session_factory) -> None:
    await _seed(
        session_factory,
        last_apply_ok=False,
        last_apply_message="Generated configuration failed `nginx -t`.",
        last_apply_output=NGINX_ERROR,
    )

    body = (await db_client.get("/api/v1/nginx/status", headers=_auth(admin_token))).json()

    assert body["ok"] is False
    assert "host not found in upstream" in body["output"]
    assert "nginx -t" in body["message"]


async def test_no_recorded_apply_is_neither_healthy_nor_failed(
    db_client, admin_token, session_factory
) -> None:
    # A fresh install, or one upgraded before any apply: the banner must not
    # claim a failure it has no evidence for.
    await _seed(session_factory)

    body = (await db_client.get("/api/v1/nginx/status", headers=_auth(admin_token))).json()

    assert body["ok"] is None


async def test_members_cannot_read_it(db_client, member_token, session_factory) -> None:
    await _seed(session_factory, last_apply_ok=False, last_apply_output=NGINX_ERROR)

    resp = await db_client.get("/api/v1/nginx/status", headers=_auth(member_token))

    assert resp.status_code == 403


def test_announcing_inside_a_running_loop_never_fails_the_apply() -> None:
    """Celery's eager mode runs the task inside the API request's event loop.

    asyncio.run refuses to start there. Found by the reload endpoint's own
    test: the announcement raised and failed an apply that had succeeded.
    """

    async def call_from_async_code() -> None:
        nginx_task._announce("config.applied")

    asyncio.run(call_from_async_code())  # must not raise
