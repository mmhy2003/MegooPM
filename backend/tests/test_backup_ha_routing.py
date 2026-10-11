"""Under HA, backups belong on one node — the one holding the directory.

BACKUP_PATH is a per-node path, not the shared mount. A backup task picked up
by another node writes a file the page never lists, and a restore picked up by
another node cannot find the file at all. CrowdSec's container tasks already
solve this by routing to the control-plane node; backups do the same.
"""

from __future__ import annotations

import pytest
from app.core.celery_app import create_celery
from app.core.config import Settings

BACKUP_TASKS = (
    "app.tasks.backup.create_backup_task",
    "app.tasks.backup.restore_backup_task",
    "app.tasks.backup.backup_tick",
)


@pytest.fixture
def ha_settings(monkeypatch):
    import app.core.celery_app as celery_module

    settings = Settings(
        ha_enabled=True,
        node_id="node-b",
        crowdsec_control_node_id="node-a",
        celery_broker_url="memory://",
        celery_result_backend="cache+memory://",
    )
    monkeypatch.setattr(celery_module, "settings", settings)
    return settings


def test_backups_run_on_the_control_plane_node(ha_settings) -> None:
    routes = create_celery().conf.task_routes

    for task in BACKUP_TASKS:
        assert task in routes, f"{task} would run on whichever node grabbed it"
        assert routes[task]["queue"] == "megoopm.node.node-a"
