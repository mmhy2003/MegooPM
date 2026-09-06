"""Noticing that CAPI credentials have gone stale, and re-registering.

Why this exists: with the community blocklist on, CrowdSec authenticates to
the central API during LAPI init and treats a rejection as **fatal**. Stored
credentials that CAPI later refuses therefore take down local detection too,
which needs CAPI for nothing — and the failure only appears at the next
restart, which may be days after the credentials went bad.

The window that matters is while the container is still up on its loaded
config. `docker exec` cannot reach a crash-looping container, so both the
check and the repair below only work before the failure turns fatal. That is
the point of surfacing it early.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.services.crowdsec import capi
from app.services.crowdsec.reload import CrowdSecReloadError, ExecResult

OK_OUTPUT = "You can successfully interact with Central API (CAPI)"
FORBIDDEN = 'level=fatal msg="unable to authenticate to Central API (CAPI): API error: Forbidden"'


def _enabled_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml.local"
    path.write_text(capi.render_config_local(capi_enabled=True), encoding="utf-8")
    return path


def _disabled_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml.local"
    path.write_text(capi.render_config_local(capi_enabled=False), encoding="utf-8")
    return path


# --- is the blocklist switched on? ---------------------------------------------


def test_the_config_says_when_the_blocklist_is_on(tmp_path: Path) -> None:
    assert capi.read_capi_enabled(_enabled_config(tmp_path)) is True
    assert capi.read_capi_enabled(_disabled_config(tmp_path)) is False


def test_a_missing_config_is_not_enabled(tmp_path: Path) -> None:
    assert capi.read_capi_enabled(tmp_path / "nope.yaml") is False


# --- the check -----------------------------------------------------------------


def test_nothing_is_checked_when_the_blocklist_is_off(tmp_path: Path) -> None:
    """No exec at all: there are no credentials to be rejected."""
    calls: list[list[str]] = []

    def _exec(cmd: list[str]) -> ExecResult:
        calls.append(cmd)
        return ExecResult(0, "")

    status = capi.check_capi_status(path=_disabled_config(tmp_path), exec=_exec)

    assert status.enabled is False
    assert status.ok is None
    assert calls == []


def test_working_credentials_report_healthy(tmp_path: Path) -> None:
    status = capi.check_capi_status(
        path=_enabled_config(tmp_path), exec=lambda cmd: ExecResult(0, OK_OUTPUT)
    )
    assert status.enabled is True
    assert status.ok is True
    assert status.detail is None


def test_rejected_credentials_say_what_is_at_stake(tmp_path: Path) -> None:
    # The operator has to understand this is not cosmetic: the container is
    # running now and will not come back from its next restart.
    status = capi.check_capi_status(
        path=_enabled_config(tmp_path), exec=lambda cmd: ExecResult(1, FORBIDDEN)
    )

    assert status.ok is False
    assert status.detail is not None
    assert "restart" in status.detail.lower()
    # And the engine's own words, so the cause is not guesswork.
    assert "Forbidden" in status.detail


def test_an_unreachable_container_is_unknown_not_broken(tmp_path: Path) -> None:
    """A container we cannot exec into tells us nothing about the credentials.

    Reporting "rejected" here would send the operator re-registering working
    credentials to fix a docker socket problem.
    """

    def _exec(cmd: list[str]) -> ExecResult:
        raise CrowdSecReloadError("cannot reach the docker daemon")

    status = capi.check_capi_status(path=_enabled_config(tmp_path), exec=_exec)

    assert status.enabled is True
    assert status.ok is None
    assert "docker daemon" in (status.detail or "")


# --- re-registering ------------------------------------------------------------


def test_registering_is_refused_while_the_blocklist_is_off(tmp_path: Path) -> None:
    result = capi.run_capi_register(
        path=_disabled_config(tmp_path),
        exec=lambda cmd: ExecResult(0, ""),
        restart=lambda: None,
        healthy=lambda: True,
    )
    assert result.ok is False
    assert "blocklist" in (result.error or "").lower()


def test_a_successful_re_registration_restarts_and_verifies(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    restarts: list[int] = []

    def _exec(cmd: list[str]) -> ExecResult:
        calls.append(cmd)
        return ExecResult(0, OK_OUTPUT if cmd == capi.CMD_STATUS else "")

    result = capi.run_capi_register(
        path=_enabled_config(tmp_path),
        exec=_exec,
        restart=lambda: restarts.append(1),
        healthy=lambda: True,
    )

    assert result.ok is True
    assert result.restarted is True
    # Register, restart, then confirm — confirming before the restart would
    # only prove the old credentials were still loaded.
    assert calls == [capi.CMD_REGISTER, capi.CMD_STATUS]
    assert restarts == [1]


def test_a_failed_registration_does_not_restart(tmp_path: Path) -> None:
    # Restarting on bad credentials is how the container ends up crash-looping.
    restarts: list[int] = []

    def _exec(cmd: list[str]) -> ExecResult:
        return ExecResult(1, 'level=fatal msg="api error: too many requests"')

    result = capi.run_capi_register(
        path=_enabled_config(tmp_path),
        exec=_exec,
        restart=lambda: restarts.append(1),
        healthy=lambda: True,
    )

    assert result.ok is False
    assert "too many requests" in (result.error or "")
    assert restarts == []


def test_a_restart_failure_is_reported(tmp_path: Path) -> None:
    def _restart() -> None:
        raise CrowdSecReloadError("permission denied on /var/run/docker.sock")

    result = capi.run_capi_register(
        path=_enabled_config(tmp_path),
        exec=lambda cmd: ExecResult(0, ""),
        restart=_restart,
        healthy=lambda: True,
    )

    assert result.ok is False
    assert "docker.sock" in (result.error or "")


def test_credentials_that_are_still_refused_after_the_restart_are_reported(
    tmp_path: Path,
) -> None:
    result = capi.run_capi_register(
        path=_enabled_config(tmp_path),
        exec=lambda cmd: ExecResult(0, "" if cmd == capi.CMD_REGISTER else FORBIDDEN),
        restart=lambda: None,
        healthy=lambda: True,
    )

    assert result.ok is False
    assert result.restarted is True
    assert "Forbidden" in (result.error or "")


def test_a_container_that_does_not_come_back_is_reported(tmp_path: Path) -> None:
    result = capi.run_capi_register(
        path=_enabled_config(tmp_path),
        exec=lambda cmd: ExecResult(0, OK_OUTPUT),
        restart=lambda: None,
        healthy=lambda: False,
    )

    assert result.ok is False
    assert "did not come back" in (result.error or "")


@pytest.mark.parametrize("output", ["", "no idea", "Forbidden"])
def test_only_the_engines_own_success_line_counts_as_working(output: str) -> None:
    """Anything but cscli's confirmation is treated as not working."""
    assert capi.parse_capi_status(ExecResult(0, output)) is False
