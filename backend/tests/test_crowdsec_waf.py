"""The WAF's request-body limit: rendering MegooPM's AppSec config, applying it.

Applying restarts CrowdSec, and protected hosts fail closed while it is down,
so the sequence matters as much as the file: test the config before the
restart, restore it if CrowdSec does not come back — and, when the reason it
did not come back is CrowdSec's own firewall refusing this address, switch
the community blocklist off rather than leave every host denying traffic.
"""

from __future__ import annotations

from pathlib import Path

from app.models.enums import AppSecBodyAction
from app.services.crowdsec import capi, waf
from app.services.crowdsec.reload import CrowdSecReloadError, ExecResult

OK = ExecResult(0, 'level=info msg="Configuration test done"')
BROKEN = ExecResult(1, 'level=fatal msg="unknown helper SetMaxBodySize"')

NEW = waf.render_body_size_config(max_body_bytes=100 * 1024 * 1024, action=AppSecBodyAction.partial)


class Engine:
    """CrowdSec as the apply sees it: restarts, and whether it comes back."""

    def __init__(self, *, healthy: list[bool] | None = None, blocked: bool | None = False):
        self.answers = list(healthy or [True])
        self.blocked = blocked
        self.restarts = 0
        self.probes = 0

    def restart(self) -> None:
        self.restarts += 1

    def healthy(self) -> bool:
        return self.answers.pop(0) if self.answers else True

    def edge_blocked(self) -> bool | None:
        self.probes += 1
        return self.blocked


def _files(tmp_path: Path, *, capi_on: bool = True) -> tuple[Path, Path, str, str]:
    body = tmp_path / "megoopm-body-size.yaml"
    local = tmp_path / "config.yaml.local"
    old_body = waf.render_body_size_config(
        max_body_bytes=waf.DEFAULT_MAX_BODY_BYTES, action=waf.DEFAULT_ACTION
    )
    old_local = capi.render_config_local(capi_enabled=capi_on)
    body.write_text(old_body, encoding="utf-8")
    local.write_text(old_local, encoding="utf-8")
    return body, local, old_body, old_local


def _apply(body: Path, local: Path, engine: Engine, *, content: str = NEW, capi_on=True, test=OK):
    return waf.run_waf_apply(
        content=content,
        path=body,
        config_local_path=local,
        capi_enabled=capi_on,
        validate=lambda: test,
        restart=engine.restart,
        healthy=engine.healthy,
        edge_blocked=engine.edge_blocked,
    )


# --- rendering -----------------------------------------------------------------


def test_render_has_the_bytes_and_the_action() -> None:
    text = waf.render_body_size_config(max_body_bytes=104857600, action=AppSecBodyAction.allow)
    assert "name: megoopm/body-size" in text
    assert "SetMaxBodySize(104857600)" in text
    assert 'SetBodySizeExceededAction("allow")' in text


def test_the_defaults_are_ten_megabytes_and_partial() -> None:
    assert waf.DEFAULT_MAX_BODY_BYTES == 10485760
    assert waf.DEFAULT_ACTION is AppSecBodyAction.partial


# --- applying ------------------------------------------------------------------


def test_unchanged_content_restarts_nothing(tmp_path: Path) -> None:
    body, local, old_body, _ = _files(tmp_path)
    engine = Engine()

    result = _apply(body, local, engine, content=old_body)

    assert result.ok is True and result.restarted is False
    assert engine.restarts == 0


def test_a_config_crowdsec_refuses_is_restored_without_a_restart(tmp_path: Path) -> None:
    body, local, old_body, _ = _files(tmp_path)
    engine = Engine()

    result = _apply(body, local, engine, test=BROKEN)

    assert result.ok is False and result.restarted is False
    assert "SetMaxBodySize" in (result.error or "")
    assert body.read_text(encoding="utf-8") == old_body
    assert engine.restarts == 0


def test_a_healthy_restart_applies(tmp_path: Path) -> None:
    body, local, _, old_local = _files(tmp_path)
    engine = Engine(healthy=[True])

    result = _apply(body, local, engine)

    assert result.ok is True and result.restarted is True
    assert result.blocklist_switched_off is False
    assert body.read_text(encoding="utf-8") == NEW
    assert local.read_text(encoding="utf-8") == old_local
    assert engine.restarts == 1 and engine.probes == 0


def test_blocked_and_down_switches_the_blocklist_off_and_keeps_the_new_limit(
    tmp_path: Path,
) -> None:
    body, local, _, _ = _files(tmp_path)
    engine = Engine(healthy=[False, True], blocked=True)

    result = _apply(body, local, engine)

    assert result.ok is True and result.blocklist_switched_off is True
    assert "online_client:" not in local.read_text(encoding="utf-8")
    assert body.read_text(encoding="utf-8") == NEW
    assert engine.restarts == 2


def test_down_but_not_blocked_rolls_back_and_leaves_the_blocklist_alone(tmp_path: Path) -> None:
    body, local, old_body, old_local = _files(tmp_path)
    engine = Engine(healthy=[False, True], blocked=False)

    result = _apply(body, local, engine)

    assert result.ok is False and result.blocklist_switched_off is False
    assert "previous limit was restored" in (result.error or "")
    assert body.read_text(encoding="utf-8") == old_body
    assert local.read_text(encoding="utf-8") == old_local
    assert engine.restarts == 2


def test_an_unanswered_probe_is_not_taken_as_a_block(tmp_path: Path) -> None:
    body, local, old_body, old_local = _files(tmp_path)
    engine = Engine(healthy=[False, True], blocked=None)

    result = _apply(body, local, engine)

    assert result.ok is False
    assert local.read_text(encoding="utf-8") == old_local
    assert body.read_text(encoding="utf-8") == old_body


def test_down_with_the_blocklist_off_never_probes(tmp_path: Path) -> None:
    body, local, old_body, _ = _files(tmp_path, capi_on=False)
    engine = Engine(healthy=[False, True], blocked=True)

    result = _apply(body, local, engine, capi_on=False)

    assert result.ok is False
    assert engine.probes == 0
    assert body.read_text(encoding="utf-8") == old_body


def test_still_down_after_the_switch_off_restores_both_files(tmp_path: Path) -> None:
    body, local, old_body, old_local = _files(tmp_path)
    engine = Engine(healthy=[False, False, True], blocked=True)

    result = _apply(body, local, engine)

    assert result.ok is False and result.blocklist_switched_off is False
    assert body.read_text(encoding="utf-8") == old_body
    assert local.read_text(encoding="utf-8") == old_local
    assert engine.restarts == 3


def test_a_failing_restart_is_reported_not_raised(tmp_path: Path) -> None:
    body, local, old_body, _ = _files(tmp_path)

    class NoDocker(Engine):
        def restart(self) -> None:
            raise CrowdSecReloadError("permission denied on /var/run/docker.sock")

    result = _apply(body, local, NoDocker())

    assert result.ok is False
    assert "docker.sock" in (result.error or "")
    assert body.read_text(encoding="utf-8") == old_body


def test_an_unreachable_container_during_the_test_writes_nothing_live(tmp_path: Path) -> None:
    body, local, old_body, _ = _files(tmp_path)
    engine = Engine()

    def _no_exec() -> ExecResult:
        raise CrowdSecReloadError("cannot reach the docker daemon")

    result = waf.run_waf_apply(
        content=NEW,
        path=body,
        config_local_path=local,
        capi_enabled=True,
        validate=_no_exec,
        restart=engine.restart,
        healthy=engine.healthy,
        edge_blocked=engine.edge_blocked,
    )

    assert result.ok is False and "docker daemon" in (result.error or "")
    assert body.read_text(encoding="utf-8") == old_body
    assert engine.restarts == 0
