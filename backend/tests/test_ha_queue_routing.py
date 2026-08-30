"""Broker topology for HA config propagation.

These are regression tests for a bug that shipped precisely because nothing
asserted the topology: ``reconcile_local_nginx`` was routed to a
:class:`kombu.common.Broadcast` (fanout exchange) queue, which does not work on
the Redis broker. The publish side wrote each message into the bound workers'
``bcast.*`` Redis *lists* while kombu's Redis transport consumes fanout queues
over *pub/sub*, so every reconcile was delivered, persisted, and never read —
silently taking the periodic backstop (routed to the same queue) with it.

The unit tests below assert the shape; ``docs/ha.md`` §4 records the end-to-end
verification against real Redis workers.
"""

from __future__ import annotations

import pytest
from app.core.celery_app import create_celery, node_queue
from app.core.config import settings

RECONCILE = "app.tasks.nginx.reconcile_local_nginx"


@pytest.fixture
def ha_app(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "ha_enabled", True)
    monkeypatch.setattr(settings, "node_id", "node-a")
    monkeypatch.setattr(settings, "ha_reconcile_interval_seconds", 15.0)
    monkeypatch.setattr(settings, "ha_reconcile_expires_seconds", None)
    return create_celery()


def test_node_consumes_a_queue_named_for_itself(ha_app) -> None:
    names = {q.name for q in ha_app.conf.task_queues}
    assert node_queue("node-a") == "megoopm.node.node-a"
    assert "megoopm.node.node-a" in names


def test_reconcile_routes_to_this_nodes_own_queue(ha_app) -> None:
    route = ha_app.amqp.router.route({}, RECONCILE)
    # An unaddressed reconcile (this node's beat tick) can only target this node.
    assert route["queue"].name == "megoopm.node.node-a"


def test_no_fanout_exchange_anywhere(ha_app) -> None:
    """The regression guard: a fanout exchange is what broke propagation."""
    for queue in ha_app.conf.task_queues:
        exchange = queue.exchange
        assert exchange is None or exchange.type != "fanout", (
            f"{queue.name} is bound to a fanout exchange; reconciles published to "
            "a fanout exchange are never consumed on the Redis broker"
        )
        assert not queue.name.startswith("bcast."), queue.name


def test_periodic_reconcile_is_scheduled_and_expires(ha_app) -> None:
    entry = ha_app.conf.beat_schedule["reconcile-nginx-across-nodes"]
    assert entry["task"] == RECONCILE
    assert entry["schedule"] == 15.0
    # Without a TTL a node offline for hours wakes to a backlog of stale no-ops.
    assert entry["options"]["expires"] == 45.0


def test_two_nodes_get_distinct_queues(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ha_enabled", True)
    monkeypatch.setattr(settings, "node_id", "node-a")
    a = create_celery().amqp.router.route({}, RECONCILE)["queue"].name
    monkeypatch.setattr(settings, "node_id", "node-b")
    b = create_celery().amqp.router.route({}, RECONCILE)["queue"].name
    # Distinct queues are what make a push addressable to one specific node.
    assert a != b == "megoopm.node.node-b"


def test_non_ha_has_no_node_queue_or_reconcile_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ha_enabled", False)
    app = create_celery()
    assert "reconcile-nginx-across-nodes" not in app.conf.beat_schedule
    names = {q.name for q in (app.conf.task_queues or ())}
    assert not any(n.startswith("megoopm.node.") for n in names)
