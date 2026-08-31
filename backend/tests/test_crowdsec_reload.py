"""Restarting CrowdSec over the docker socket, and the apply/rollback sequence.

Restarting CrowdSec makes AppSec unreachable, and the bouncer runs
``APPSEC_FAILURE_ACTION=deny``, so every protected host fails closed while it is
down. That makes two behaviours here safety properties rather than niceties: an
unchanged render must not restart anything, and a file CrowdSec cannot load must
be put back.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from app.services.crowdsec.reload import CrowdSecReloadError, restart_container
from app.services.crowdsec.whitelists import (
    WhitelistDoc,
    content_digest,
    render_whitelists,
)
from app.tasks.crowdsec import apply_whitelists_to_disk

DOC = WhitelistDoc(
    name="internal",
    reason="internal backends trip appsec generic rules",
    description="",
    ips=["10.10.0.14"],
    cidrs=[],
)


# --- docker socket client -------------------------------------------------


def test_posts_a_restart_for_the_named_container() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["method"] = request.method
        return httpx.Response(204)

    restart_container(
        "megoopm-crowdsec-1",
        socket_path="/var/run/docker.sock",
        timeout_seconds=30,
        transport=httpx.MockTransport(handler),
    )
    assert seen["method"] == "POST"
    assert seen["path"].endswith("/containers/megoopm-crowdsec-1/restart")


def test_already_running_is_not_an_error() -> None:
    # Docker answers 304 when the container is already in the requested state.
    restart_container(
        "megoopm-crowdsec-1",
        socket_path="/var/run/docker.sock",
        timeout_seconds=30,
        transport=httpx.MockTransport(lambda _r: httpx.Response(304)),
    )


def test_missing_container_names_what_it_tried() -> None:
    # A wrong CROWDSEC_CONTAINER_NAME is the likeliest misconfiguration, so the
    # error has to say which name failed rather than just "404".
    with pytest.raises(CrowdSecReloadError, match="megoopm-crowdsec-1"):
        restart_container(
            "megoopm-crowdsec-1",
            socket_path="/var/run/docker.sock",
            timeout_seconds=30,
            transport=httpx.MockTransport(
                lambda _r: httpx.Response(404, json={"message": "No such container"})
            ),
        )


def test_socket_error_names_the_socket_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Permission denied")

    with pytest.raises(CrowdSecReloadError, match="/var/run/docker.sock"):
        restart_container(
            "megoopm-crowdsec-1",
            socket_path="/var/run/docker.sock",
            timeout_seconds=30,
            transport=httpx.MockTransport(handler),
        )


# --- apply / rollback sequence --------------------------------------------


class _Recorder:
    """Stands in for the restart + health-check pair."""

    def __init__(self, *, healthy: bool) -> None:
        self.restarts = 0
        self._healthy = healthy

    def restart(self) -> None:
        self.restarts += 1

    def healthy(self) -> bool:
        return self._healthy


def test_writes_renders_and_restarts(tmp_path: Path) -> None:
    path = tmp_path / "megoopm.yaml"
    path.write_text("# seed\n", encoding="utf-8")
    rec = _Recorder(healthy=True)

    result = apply_whitelists_to_disk(
        [DOC], path=path, applied_digest=None, restart=rec.restart, healthy=rec.healthy
    )

    assert result.ok is True
    assert rec.restarts == 1
    assert "megoopm/wl-internal" in path.read_text(encoding="utf-8")
    assert result.digest == content_digest(render_whitelists([DOC]))


def test_unchanged_content_does_not_restart_crowdsec(tmp_path: Path) -> None:
    # A restart is a few seconds of fail-closed denial on every protected host.
    # A save that changes nothing must not cost that.
    path = tmp_path / "megoopm.yaml"
    content = render_whitelists([DOC])
    path.write_text(content, encoding="utf-8")
    rec = _Recorder(healthy=True)

    result = apply_whitelists_to_disk(
        [DOC],
        path=path,
        applied_digest=content_digest(content),
        restart=rec.restart,
        healthy=rec.healthy,
    )

    assert result.ok is True
    assert rec.restarts == 0


def test_a_matching_digest_with_a_stale_file_still_rewrites(tmp_path: Path) -> None:
    # Someone edited the file by hand, or a rollback left it behind. The digest
    # alone would short-circuit and leave CrowdSec on the wrong content.
    path = tmp_path / "megoopm.yaml"
    path.write_text("# tampered\n", encoding="utf-8")
    rec = _Recorder(healthy=True)

    result = apply_whitelists_to_disk(
        [DOC],
        path=path,
        applied_digest=content_digest(render_whitelists([DOC])),
        restart=rec.restart,
        healthy=rec.healthy,
    )

    assert result.ok is True
    assert rec.restarts == 1
    assert "megoopm/wl-internal" in path.read_text(encoding="utf-8")


def test_rolls_back_when_crowdsec_does_not_come_back(tmp_path: Path) -> None:
    # A parser file CrowdSec cannot load stops it starting, and with
    # APPSEC_FAILURE_ACTION=deny that denies every request on every protected
    # host indefinitely. Rollback bounds it to the health timeout.
    path = tmp_path / "megoopm.yaml"
    previous = "# Managed by MegooPM - no whitelists defined.\n"
    path.write_text(previous, encoding="utf-8")
    rec = _Recorder(healthy=False)

    result = apply_whitelists_to_disk(
        [DOC], path=path, applied_digest=None, restart=rec.restart, healthy=rec.healthy
    )

    assert result.ok is False
    assert "did not come back" in result.error
    assert path.read_text(encoding="utf-8") == previous
    assert rec.restarts == 2  # once onto the new file, once back onto the old


def test_rollback_preserves_the_inode(tmp_path: Path) -> None:
    path = tmp_path / "megoopm.yaml"
    path.write_text("# seed\n", encoding="utf-8")
    before = path.stat().st_ino
    rec = _Recorder(healthy=False)

    apply_whitelists_to_disk(
        [DOC], path=path, applied_digest=None, restart=rec.restart, healthy=rec.healthy
    )

    assert path.stat().st_ino == before


def test_a_failed_restart_puts_the_previous_file_back(tmp_path: Path) -> None:
    path = tmp_path / "megoopm.yaml"
    previous = "# seed\n"
    path.write_text(previous, encoding="utf-8")

    def boom() -> None:
        raise CrowdSecReloadError("docker socket not mounted")

    result = apply_whitelists_to_disk(
        [DOC], path=path, applied_digest=None, restart=boom, healthy=lambda: True
    )

    assert result.ok is False
    assert "docker socket not mounted" in result.error
    assert path.read_text(encoding="utf-8") == previous


def test_invalid_entry_never_reaches_the_file(tmp_path: Path) -> None:
    path = tmp_path / "megoopm.yaml"
    path.write_text("# seed\n", encoding="utf-8")
    bad = WhitelistDoc(
        name="bad", reason="typo", description="", ips=["10.10.0.999"], cidrs=[]
    )
    rec = _Recorder(healthy=True)

    result = apply_whitelists_to_disk(
        [bad], path=path, applied_digest=None, restart=rec.restart, healthy=rec.healthy
    )

    assert result.ok is False
    assert "10.10.0.999" in result.error
    assert path.read_text(encoding="utf-8") == "# seed\n"
    assert rec.restarts == 0
