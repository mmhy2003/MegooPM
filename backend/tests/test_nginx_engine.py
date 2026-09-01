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
from app.services.nginx.renderer import DEFAULT_SITE_CONF, DEFAULT_SITE_HTML
from app.services.nginx.state import (
    BackendSpec,
    DefaultSiteSpec,
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
        http_upstreams=(pool,),
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
        http_upstreams=_state().http_upstreams,
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
        http_upstreams=_state().http_upstreams,
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


# --- Default site ----------------------------------------------------------


def _state_with_default(site: DefaultSiteSpec) -> DesiredState:
    """The module's usual proxy-host state, plus a default site."""
    base = _state()
    return DesiredState(
        proxy_hosts=base.proxy_hosts,
        http_upstreams=base.http_upstreams,
        default_site=site,
    )


def test_apply_writes_the_default_site_to_its_own_directory(tmp_path: Path) -> None:
    confd = tmp_path / "conf.d"
    default_dir = tmp_path / "default"
    controller = FakeController()

    result = apply_config(
        DesiredState(default_site=DefaultSiteSpec(mode="no_response")),
        confd_dir=confd,
        controller=controller,
        default_dir=default_dir,
    )

    assert result.valid and result.changed
    assert "return 444;" in (default_dir / DEFAULT_SITE_CONF).read_text()
    # One validation covering every directory, not one per directory.
    assert controller.tests == 1


def test_apply_removes_the_html_when_the_mode_stops_needing_it(tmp_path: Path) -> None:
    """A stale megoopm-default.html would be served by nothing but still sit there."""
    confd = tmp_path / "conf.d"
    default_dir = tmp_path / "default"

    apply_config(
        DesiredState(default_site=DefaultSiteSpec(mode="custom_page", html="<p>x</p>")),
        confd_dir=confd,
        controller=FakeController(),
        default_dir=default_dir,
    )
    assert (default_dir / DEFAULT_SITE_HTML).exists()

    apply_config(
        DesiredState(default_site=DefaultSiteSpec(mode="not_found")),
        confd_dir=confd,
        controller=FakeController(),
        default_dir=default_dir,
    )
    assert not (default_dir / DEFAULT_SITE_HTML).exists()


def test_a_bad_default_site_rolls_back_the_other_directories(tmp_path: Path) -> None:
    """All targets share one nginx -t, so none may half-apply."""
    confd = tmp_path / "conf.d"
    default_dir = tmp_path / "default"

    apply_config(
        DesiredState(default_site=DefaultSiteSpec(mode="not_found")),
        confd_dir=confd,
        controller=FakeController(),
        default_dir=default_dir,
    )
    good = (default_dir / DEFAULT_SITE_CONF).read_text()

    result = apply_config(
        _state_with_default(DefaultSiteSpec(mode="no_response")),
        confd_dir=confd,
        controller=FakeController(test_ok=False),
        default_dir=default_dir,
    )
    assert result.rolled_back and not result.valid
    assert (default_dir / DEFAULT_SITE_CONF).read_text() == good
    assert not list(confd.glob("megoopm-proxy-*.conf"))


def test_apply_without_a_default_dir_touches_nothing_new(tmp_path: Path) -> None:
    """Callers that do not opt in keep their current behaviour exactly."""
    confd = tmp_path / "conf.d"
    result = apply_config(
        DesiredState(default_site=DefaultSiteSpec(mode="no_response")),
        confd_dir=confd,
        controller=FakeController(),
    )
    assert result.valid
    assert not (tmp_path / "default").exists()
