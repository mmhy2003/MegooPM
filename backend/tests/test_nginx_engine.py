"""Engine tests: atomic apply, idempotency, and validation rollback.

These use a fake :class:`NginxController` and a temp ``conf.d`` directory, so
the full write/validate/reload/rollback state machine is exercised without a
real nginx, root, or live traffic.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from app.services.nginx import apply_config, render_config
from app.services.nginx.controller import CommandResult, ShellNginxController
from app.services.nginx.state import (
    BackendSpec,
    DesiredState,
    ProxyHostSpec,
    UpstreamSpec,
)


class FakeController:
    """Records calls and returns configurable outcomes for test/reload."""

    def __init__(self, test_ok: bool = True, reload_ok: bool = True) -> None:
        self.test_ok = test_ok
        self.reload_ok = reload_ok
        self.tests = 0
        self.reloads = 0

    def test(self) -> CommandResult:
        self.tests += 1
        return CommandResult(ok=self.test_ok, output="ok" if self.test_ok else "invalid config")

    def reload(self) -> CommandResult:
        self.reloads += 1
        return CommandResult(ok=self.reload_ok, output="reloaded" if self.reload_ok else "boom")


def _state() -> DesiredState:
    pool = UpstreamSpec(id=1, name="p", backends=(BackendSpec(host="10.0.0.1", port=80),))
    return DesiredState(
        proxy_hosts=(ProxyHostSpec(id=1, domain_names=("a.example.com",), upstream_id=1),),
        upstreams=(pool,),
    )


def test_apply_writes_files_and_reloads(tmp_path: Path) -> None:
    ctrl = FakeController()
    result = apply_config(_state(), confd_dir=tmp_path, controller=ctrl)

    assert result.changed and result.valid and result.reloaded
    assert not result.rolled_back
    assert ctrl.tests == 1 and ctrl.reloads == 1
    assert (tmp_path / "megoopm-proxy-1.conf").exists()
    assert (tmp_path / "megoopm-upstream-1.conf").exists()


def test_apply_is_idempotent_and_skips_reload_when_unchanged(tmp_path: Path) -> None:
    ctrl = FakeController()
    apply_config(_state(), confd_dir=tmp_path, controller=ctrl)

    ctrl2 = FakeController()
    result = apply_config(_state(), confd_dir=tmp_path, controller=ctrl2)

    assert not result.changed and result.valid and not result.reloaded
    # No changes → nginx is never touched.
    assert ctrl2.tests == 0 and ctrl2.reloads == 0


def test_invalid_config_is_rejected_and_rolled_back(tmp_path: Path) -> None:
    # Seed a known-good config first.
    apply_config(_state(), confd_dir=tmp_path, controller=FakeController())
    good = (tmp_path / "megoopm-proxy-1.conf").read_text()

    # Now apply a *different* state under a controller whose `nginx -t` fails.
    changed = DesiredState(
        proxy_hosts=(ProxyHostSpec(id=2, domain_names=("b.example.com",), upstream_id=1),),
        upstreams=_state().upstreams,
    )
    ctrl = FakeController(test_ok=False)
    result = apply_config(changed, confd_dir=tmp_path, controller=ctrl)

    assert not result.valid and result.rolled_back and not result.reloaded
    assert ctrl.reloads == 0  # a broken config never reaches a reload
    # Disk is restored exactly to the previous good state.
    assert (tmp_path / "megoopm-proxy-1.conf").read_text() == good
    assert not (tmp_path / "megoopm-proxy-2.conf").exists()


def test_reload_failure_rolls_back_to_previous(tmp_path: Path) -> None:
    apply_config(_state(), confd_dir=tmp_path, controller=FakeController())

    changed = DesiredState(
        proxy_hosts=(ProxyHostSpec(id=1, domain_names=("c.example.com",), upstream_id=1),),
        upstreams=_state().upstreams,
    )
    ctrl = FakeController(test_ok=True, reload_ok=False)
    result = apply_config(changed, confd_dir=tmp_path, controller=ctrl)

    assert result.valid and not result.reloaded and result.rolled_back
    # Rolled back to the a.example.com config, not the failed c.example.com one.
    assert "a.example.com" in (tmp_path / "megoopm-proxy-1.conf").read_text()


def test_unmanaged_files_are_left_untouched(tmp_path: Path) -> None:
    manual = tmp_path / "operator-custom.conf"
    manual.write_text("# hand written\n")

    apply_config(_state(), confd_dir=tmp_path, controller=FakeController())

    assert manual.exists() and manual.read_text() == "# hand written\n"


def test_stale_managed_files_are_removed(tmp_path: Path) -> None:
    apply_config(_state(), confd_dir=tmp_path, controller=FakeController())
    assert (tmp_path / "megoopm-proxy-1.conf").exists()

    # Empty desired state → the previously managed file should be deleted.
    apply_config(DesiredState(), confd_dir=tmp_path, controller=FakeController())
    assert not (tmp_path / "megoopm-proxy-1.conf").exists()


@pytest.mark.skipif(shutil.which("nginx") is None, reason="nginx binary not installed")
def test_generated_config_passes_real_nginx_t(tmp_path: Path) -> None:
    """When nginx is available, the generated files must pass a real `nginx -t`."""
    confd = tmp_path / "conf.d"
    confd.mkdir()
    for name, content in render_config(_state()).items():
        (confd / name).write_text(content)

    main_conf = tmp_path / "nginx.conf"
    main_conf.write_text(
        "events {}\n"
        "http {\n"
        "  map $http_upgrade $connection_upgrade { default upgrade; '' close; }\n"
        f"  include {confd}/*.conf;\n"
        "}\n"
    )
    ctrl = ShellNginxController(test_command=f"nginx -t -c {main_conf}")
    assert ctrl.test().ok
