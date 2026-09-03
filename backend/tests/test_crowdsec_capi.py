"""The community-blocklist switch: render, parse, and the flow with fakes."""

from __future__ import annotations

from pathlib import Path

from app.services.crowdsec import capi
from app.services.crowdsec.reload import CrowdSecReloadError, ExecResult

# --- render -------------------------------------------------------------------


def test_off_keeps_the_auto_registration_block_only() -> None:
    text = capi.render_config_local(capi_enabled=False)
    assert "auto_registration:" in text
    assert "${CROWDSEC_REGISTRATION_TOKEN}" in text
    assert "online_client" not in text


def test_on_adds_the_online_client_block() -> None:
    text = capi.render_config_local(capi_enabled=True)
    assert "auto_registration:" in text
    assert f"credentials_path: {capi.CREDENTIALS_PATH}" in text
    assert "sharing: true" in text and "community: true" in text and "blocklists: true" in text


def test_render_is_deterministic() -> None:
    assert capi.render_config_local(capi_enabled=True) == capi.render_config_local(
        capi_enabled=True
    )


# --- status -----------------------------------------------------------------------


def test_parse_capi_status() -> None:
    ok = ExecResult(
        0, "Loaded credentials\nYou can successfully interact with Central API (CAPI)\n"
    )
    assert capi.parse_capi_status(ok) is True
    assert (
        capi.parse_capi_status(ExecResult(1, 'level=fatal msg="no configuration for Central API"'))
        is False
    )
    assert capi.parse_capi_status(ExecResult(0, "something else")) is False


# --- the flow ---------------------------------------------------------------------


class FakeContainer:
    def __init__(self, *, has_credentials: bool, register_ok: bool = True, status_ok: bool = True):
        self.has_credentials = has_credentials
        self.register_ok = register_ok
        self.status_ok = status_ok
        self.ran: list[list[str]] = []
        self.restarts = 0

    def exec(self, argv: list[str]) -> ExecResult:
        self.ran.append(argv)
        if argv == capi.CMD_HAS_CREDENTIALS:
            return ExecResult(0 if self.has_credentials else 1, "")
        if argv == capi.CMD_REGISTER:
            if self.register_ok:
                self.has_credentials = True
                return ExecResult(0, "Central API credentials written")
            return ExecResult(1, 'level=fatal msg="dial tcp: no route to host"')
        if argv == capi.CMD_STATUS:
            if self.status_ok:
                return ExecResult(0, "You can successfully interact with Central API (CAPI)")
            return ExecResult(1, "nope")
        raise AssertionError(argv)

    def restart(self) -> None:
        self.restarts += 1


def _seed(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml.local"
    path.write_text(capi.render_config_local(capi_enabled=False), encoding="utf-8")
    return path


def test_enabling_registers_writes_restarts_and_verifies(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    c = FakeContainer(has_credentials=False)
    result = capi.run_capi_apply(
        enabled=True, path=path, exec=c.exec, restart=c.restart, healthy=lambda: True
    )
    assert result.ok and result.restarted and result.enabled
    assert capi.CMD_REGISTER in c.ran and capi.CMD_STATUS in c.ran
    assert "online_client" in path.read_text(encoding="utf-8")
    assert c.restarts == 1
    # The override was on disk BEFORE register ran: cscli refuses otherwise.
    assert c.ran.index(capi.CMD_REGISTER) > 0


def test_enabling_with_credentials_present_does_not_register_again(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    c = FakeContainer(has_credentials=True)
    result = capi.run_capi_apply(
        enabled=True, path=path, exec=c.exec, restart=c.restart, healthy=lambda: True
    )
    assert result.ok and capi.CMD_REGISTER not in c.ran


def test_unchanged_content_does_nothing(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    c = FakeContainer(has_credentials=True)
    result = capi.run_capi_apply(
        enabled=False, path=path, exec=c.exec, restart=c.restart, healthy=lambda: True
    )
    assert result.ok and not result.restarted and c.restarts == 0 and c.ran == []


def test_register_failure_restores_the_file_without_a_restart(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    before = path.read_text(encoding="utf-8")
    c = FakeContainer(has_credentials=False, register_ok=False)
    result = capi.run_capi_apply(
        enabled=True, path=path, exec=c.exec, restart=c.restart, healthy=lambda: True
    )
    assert not result.ok and not result.restarted
    assert "no route to host" in (result.error or "")
    assert path.read_text(encoding="utf-8") == before
    assert c.restarts == 0


def test_unhealthy_after_enable_rolls_back(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    before = path.read_text(encoding="utf-8")
    c = FakeContainer(has_credentials=True)
    result = capi.run_capi_apply(
        enabled=True, path=path, exec=c.exec, restart=c.restart, healthy=lambda: False
    )
    assert not result.ok and result.restarted
    assert path.read_text(encoding="utf-8") == before
    assert c.restarts == 2


def test_status_failure_after_enable_rolls_back(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    c = FakeContainer(has_credentials=True, status_ok=False)
    result = capi.run_capi_apply(
        enabled=True, path=path, exec=c.exec, restart=c.restart, healthy=lambda: True
    )
    assert not result.ok and "capi status" in (result.error or "").lower()
    assert "online_client" not in path.read_text(encoding="utf-8")


def test_disabling_writes_and_restarts_without_status_check(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml.local"
    path.write_text(capi.render_config_local(capi_enabled=True), encoding="utf-8")
    c = FakeContainer(has_credentials=True)
    result = capi.run_capi_apply(
        enabled=False, path=path, exec=c.exec, restart=c.restart, healthy=lambda: True
    )
    assert result.ok and result.restarted and not result.enabled
    assert capi.CMD_STATUS not in c.ran
    assert "online_client" not in path.read_text(encoding="utf-8")


def test_exec_failure_is_reported(tmp_path: Path) -> None:
    path = _seed(tmp_path)

    def broken(argv: list[str]) -> ExecResult:
        raise CrowdSecReloadError("Could not reach the docker daemon")

    result = capi.run_capi_apply(
        enabled=True, path=path, exec=broken, restart=lambda: None, healthy=lambda: True
    )
    assert not result.ok and "docker daemon" in (result.error or "")
    assert "online_client" not in path.read_text(encoding="utf-8")
