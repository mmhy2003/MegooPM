"""The flag that makes the API stand still during a restore.

While a restore runs, every write would land in a database that is about to be
replaced — or worse, in one mid-replacement. The flag lives in Redis because
the worker sets it and the API reads it.
"""

from __future__ import annotations

import fakeredis
import pytest
from app.services.backup import gate


@pytest.fixture
def client():
    return fakeredis.FakeStrictRedis()


def test_nothing_is_running_by_default(client) -> None:
    assert gate.restore_status(client)["running"] is False


def test_a_restore_reports_its_step(client) -> None:
    gate.begin_restore(client, "verifying")
    gate.set_step(client, "restoring the database")

    status = gate.restore_status(client)

    assert status["running"] is True
    assert status["step"] == "restoring the database"
    assert status["started_at"]


def test_the_flag_clears_when_the_restore_ends(client) -> None:
    gate.begin_restore(client, "verifying")
    gate.end_restore(client)

    assert gate.restore_status(client)["running"] is False


def test_a_failure_is_remembered_after_the_flag_clears(client) -> None:
    # The admin's browser is blocked on this status; it has to learn why the
    # restore stopped, not just that it did.
    gate.begin_restore(client, "verifying")
    gate.end_restore(client, error="this backup was made with a different SECRET_KEY")

    status = gate.restore_status(client)

    assert status["running"] is False
    assert "SECRET_KEY" in status["error"]
