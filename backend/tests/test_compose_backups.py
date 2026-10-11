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


def test_the_example_env_documents_the_host_path() -> None:
    path = REPO_ROOT / ".env.example"
    if not path.exists():  # pragma: no cover - the backend container mounts only backend/
        pytest.skip(f"{path} not present")
    assert "BACKUP_PATH=" in path.read_text(encoding="utf-8")
