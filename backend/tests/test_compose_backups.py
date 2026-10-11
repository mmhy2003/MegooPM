"""Backup wiring in the compose files.

The backend lists and streams backups; the worker writes them. Both need the
same directory, or a backup an admin takes is invisible to the page that took
it — a failure nobody notices until a restore is needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MOUNT = "${BACKUP_PATH:-./backups}:/backups"


def _services(compose_file: str) -> dict:
    path = REPO_ROOT / compose_file
    if not path.exists():  # pragma: no cover - partial checkout
        pytest.skip(f"{compose_file} not present at {REPO_ROOT}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))["services"]


@pytest.mark.parametrize("compose_file", ["docker-compose.yml", "docker-compose.dev.yml"])
@pytest.mark.parametrize("service", ["backend", "worker"])
def test_the_backup_directory_is_mounted(compose_file: str, service: str) -> None:
    assert MOUNT in _services(compose_file)[service]["volumes"]


@pytest.mark.parametrize("template", [".env.example", ".env.ha.example"])
def test_every_env_template_documents_the_host_path(template: str) -> None:
    """Both topologies need it, and HA needs it most.

    Under HA the backup tasks run on the control-plane node and BACKUP_PATH is
    that node's own path, not the shared mount. An operator who never sees the
    variable has no way to know where the backups are — or that they are not
    on the shared storage everything else uses.
    """
    path = REPO_ROOT / template
    if not path.exists():  # pragma: no cover - the backend container mounts only backend/
        pytest.skip(f"{path} not present")
    assert "BACKUP_PATH=" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("compose_file", ["docker-compose.yml", "docker-compose.dev.yml"])
def test_data_init_makes_the_backup_directory_writable(compose_file: str) -> None:
    """The containers run as uid 1000; dockerd creates a bind mount as root.

    Without this, the first backup on a fresh Linux install fails with
    "Permission denied" and keeps failing — and the upload route's own cleanup
    then fails too, turning a 413 into a 500. Docker Desktop on Windows hides
    it, which is why the manual check did not catch it.
    """
    init = _services(compose_file)["data-init"]
    command = " ".join(init["command"]) if isinstance(init["command"], list) else init["command"]
    assert "/backups" in command, "data-init must create and chown /backups"
    assert MOUNT in init["volumes"]


def test_ha_requires_the_backup_path_rather_than_defaulting_per_node() -> None:
    """A default here is a trap: every node gets its own directory.

    The worker writes on one node and the backend that answers the page reads
    another's, so a backup exists on disk and the UI shows nothing. HA already
    treats SHARED_DATA_PATH this way — a missing value stops the node instead
    of quietly inventing a local one.
    """
    services = _services("docker-compose.ha.yml")
    mounts = [
        mount
        for service in ("data-init", "backend", "worker")
        for mount in services[service]["volumes"]
        if isinstance(mount, str) and mount.endswith(":/backups")
    ]

    assert len(mounts) == 3, mounts
    for mount in mounts:
        assert "BACKUP_PATH:?" in mount, f"{mount} would silently default per node"


def test_the_ha_template_points_at_the_shared_mount() -> None:
    path = REPO_ROOT / ".env.ha.example"
    if not path.exists():  # pragma: no cover - the backend container mounts only backend/
        pytest.skip(f"{path} not present")
    text = path.read_text(encoding="utf-8")
    shared = next(
        line.split("=", 1)[1].strip()
        for line in text.splitlines()
        if line.startswith("SHARED_DATA_PATH=")
    )
    backups = next(
        line.split("=", 1)[1].strip()
        for line in text.splitlines()
        if line.startswith("BACKUP_PATH=")
    )

    assert backups.startswith(shared), (
        f"BACKUP_PATH={backups} is not on the shared mount {shared}: "
        "each node would list its own directory"
    )
