"""A single node is one node, however often its containers are recreated.

Without NODE_ID a node was named after its container's hostname, and a
container gets a new hostname every time it is recreated. On a single-node
install each deploy therefore left a metrics row behind under a dead name; once
those aged out, the dashboard reported "2 nodes not reporting — totals exclude
them" on a server that only ever had one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.core.config import Settings
from app.models.node_metrics import NodeMetrics
from app.services.dashboard.metrics import record_sample
from app.services.nginx.stub_status import StubStatus
from sqlalchemy import select

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
SAMPLE = StubStatus(active=3, accepted=10, handled=10, requests=40)


async def test_without_ha_the_node_has_a_stable_name() -> None:
    # The same name across recreations, so the same metrics row.
    assert Settings(ha_enabled=False, node_id=None).effective_node_id == "local"


async def test_ha_keeps_the_hostname_fallback() -> None:
    # HA names nodes explicitly; the hostname fallback stays as it was there.
    import socket

    assert Settings(ha_enabled=True, node_id=None).effective_node_id == socket.gethostname()


async def test_an_explicit_node_id_always_wins() -> None:
    assert Settings(ha_enabled=False, node_id="edge-1").effective_node_id == "edge-1"


async def test_a_single_node_scrape_removes_rows_left_by_old_names(session_factory) -> None:
    async with session_factory() as session:
        for name in ("5f7b975a6400", "94725241ca7c"):
            session.add(
                NodeMetrics(
                    node_id=name,
                    active_connections=1,
                    requests_total=5,
                    requests_per_second=0.0,
                    sampled_at=NOW - timedelta(hours=1),
                )
            )
        await session.commit()

        await record_sample(session, "local", SAMPLE, now=NOW, prune_others=True)

        names = (await session.scalars(select(NodeMetrics.node_id))).all()
    assert names == ["local"]


async def test_an_ha_scrape_leaves_the_other_nodes_alone(session_factory) -> None:
    async with session_factory() as session:
        session.add(
            NodeMetrics(
                node_id="node-b",
                active_connections=1,
                requests_total=5,
                requests_per_second=0.0,
                sampled_at=NOW,
            )
        )
        await session.commit()

        await record_sample(session, "node-a", SAMPLE, now=NOW)

        names = sorted((await session.scalars(select(NodeMetrics.node_id))).all())
    assert names == ["node-a", "node-b"]


async def test_the_scraper_prunes_only_without_ha(monkeypatch) -> None:
    from contextlib import asynccontextmanager

    from app.tasks import metrics

    seen: dict[str, object] = {}

    async def fake_fetch(url: str) -> str:
        return "Active connections: 3 \nserver accepts handled requests\n 10 10 40 \n"

    async def fake_record(session, node_id, sample, *, now, prune_others=False):
        seen["prune_others"] = prune_others

    @asynccontextmanager
    async def factory():
        yield object()

    monkeypatch.setattr(metrics, "_fetch", fake_fetch)
    monkeypatch.setattr(metrics, "record_sample", fake_record)

    monkeypatch.setattr(metrics.settings, "ha_enabled", False)
    await metrics._scrape_async(session_factory=factory)
    assert seen["prune_others"] is True

    monkeypatch.setattr(metrics.settings, "ha_enabled", True)
    await metrics._scrape_async(session_factory=factory)
    assert seen["prune_others"] is False
