"""Under HA, the WAF apply runs where the CrowdSec container and the socket are.

It writes the file CrowdSec reads and restarts the container, like the
whitelist and CAPI applies; picked up by another node's worker, it would
fail to reach the docker socket every time.
"""

from __future__ import annotations

from app.core.celery_app import create_celery, node_queue
from app.core.config import Settings


def test_the_waf_apply_runs_on_the_control_plane_node(monkeypatch) -> None:
    import app.core.celery_app as celery_module

    settings = Settings(
        ha_enabled=True,
        node_id="node-b",
        crowdsec_control_node_id="node-a",
        celery_broker_url="memory://",
        celery_result_backend="cache+memory://",
    )
    monkeypatch.setattr(celery_module, "settings", settings)

    routes = create_celery().conf.task_routes

    assert routes["app.tasks.crowdsec.apply_waf_settings"] == {"queue": node_queue("node-a")}
