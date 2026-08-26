"""Engine tests for MEG-24: streams reconcile into a separate directory.

Streams live in nginx's top-level ``stream {}`` context, so ``apply_config``
writes them to a ``stream_dir`` distinct from the http{} ``conf.d`` — but under
the same lock, one ``nginx -t``, and a shared rollback. These use a fake
controller and temp dirs, so no real nginx is needed.
"""

from __future__ import annotations

from pathlib import Path

from app.services.nginx import apply_config
from app.services.nginx.controller import CommandResult
from app.services.nginx.state import (
    BackendSpec,
    DeadHostSpec,
    DesiredState,
    ProxyHostSpec,
    StreamSpec,
    UpstreamSpec,
)


class FakeController:
    def __init__(self, test_ok: bool = True, reload_ok: bool = True) -> None:
        self.test_ok = test_ok
        self.reload_ok = reload_ok
        self.tests = 0
        self.reloads = 0

    def test(self) -> CommandResult:
        self.tests += 1
        return CommandResult(ok=self.test_ok, output="ok" if self.test_ok else "invalid")

    def reload(self) -> CommandResult:
        self.reloads += 1
        return CommandResult(ok=self.reload_ok, output="reloaded" if self.reload_ok else "boom")


def _state() -> DesiredState:
    pool = UpstreamSpec(id=1, name="p", backends=(BackendSpec(host="10.0.0.1", port=80),))
    return DesiredState(
        proxy_hosts=(ProxyHostSpec(id=1, domain_names=("a.example.com",), upstream_id=1),),
        upstreams=(pool,),
        dead_hosts=(DeadHostSpec(id=1, domain_names=("dead.example.com",)),),
        streams=(StreamSpec(id=1, incoming_port=5432, forward_host="10.0.0.5", forward_port=5432),),
    )


def test_streams_written_to_stream_dir_not_confd(tmp_path: Path) -> None:
    confd = tmp_path / "conf.d"
    streamd = confd / "stream"
    result = apply_config(
        _state(), confd_dir=confd, controller=FakeController(), stream_dir=streamd
    )

    assert result.changed and result.reloaded
    # HTTP-context files land in conf.d; the stream file lands in stream_dir.
    assert (confd / "megoopm-proxy-1.conf").exists()
    assert (confd / "megoopm-dead-1.conf").exists()
    assert not (confd / "megoopm-stream-1.conf").exists()
    assert (streamd / "megoopm-stream-1.conf").exists()
    # managed_files aggregates both directories.
    assert "megoopm-stream-1.conf" in result.managed_files
    assert "megoopm-proxy-1.conf" in result.managed_files


def test_stream_only_change_triggers_reload(tmp_path: Path) -> None:
    confd = tmp_path / "conf.d"
    streamd = confd / "stream"
    apply_config(_state(), confd_dir=confd, controller=FakeController(), stream_dir=streamd)

    # Change only the stream's forward target: http files identical, stream differs.
    changed = DesiredState(
        proxy_hosts=_state().proxy_hosts,
        upstreams=_state().upstreams,
        dead_hosts=_state().dead_hosts,
        streams=(StreamSpec(id=1, incoming_port=5432, forward_host="10.0.0.9", forward_port=5432),),
    )
    ctrl = FakeController()
    result = apply_config(changed, confd_dir=confd, controller=ctrl, stream_dir=streamd)

    assert result.changed and ctrl.reloads == 1
    assert "10.0.0.9" in (streamd / "megoopm-stream-1.conf").read_text()


def test_idempotent_across_both_dirs(tmp_path: Path) -> None:
    confd = tmp_path / "conf.d"
    streamd = confd / "stream"
    apply_config(_state(), confd_dir=confd, controller=FakeController(), stream_dir=streamd)

    ctrl = FakeController()
    result = apply_config(_state(), confd_dir=confd, controller=ctrl, stream_dir=streamd)
    assert not result.changed and not result.reloaded
    assert ctrl.tests == 0 and ctrl.reloads == 0


def test_invalid_config_rolls_back_both_dirs(tmp_path: Path) -> None:
    confd = tmp_path / "conf.d"
    streamd = confd / "stream"
    apply_config(_state(), confd_dir=confd, controller=FakeController(), stream_dir=streamd)
    good_stream = (streamd / "megoopm-stream-1.conf").read_text()

    changed = DesiredState(
        streams=(StreamSpec(id=2, incoming_port=6000, forward_host="10.0.0.2", forward_port=6000),),
    )
    ctrl = FakeController(test_ok=False)
    result = apply_config(changed, confd_dir=confd, controller=ctrl, stream_dir=streamd)

    assert not result.valid and result.rolled_back and ctrl.reloads == 0
    # Both dirs restored: the new stream is gone, the old http+stream files remain.
    assert not (streamd / "megoopm-stream-2.conf").exists()
    assert (streamd / "megoopm-stream-1.conf").read_text() == good_stream
    assert (confd / "megoopm-proxy-1.conf").exists()
