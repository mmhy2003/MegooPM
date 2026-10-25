"""Security → Settings: the WAF body limit's status and setting.

SQLite-backed; Redis and the broker are faked like the Updates tab's tests.
"""

from __future__ import annotations

import pytest
from app.api.routes import crowdsec as crowdsec_routes
from app.models.instance_settings import InstanceSettings
from httpx import AsyncClient
from sqlalchemy import select

WAF = "/api/v1/crowdsec/waf"
SAVE = "/api/v1/settings/crowdsec-waf"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class FakeRedis:
    def __init__(self, keys: set[str] | None = None) -> None:
        self.keys = keys or set()

    async def exists(self, *names: str) -> int:
        return sum(1 for n in names if n in self.keys)

    async def aclose(self) -> None:
        pass


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        crowdsec_routes.celery_app, "send_task", lambda name, **kw: calls.append((name, kw))
    )
    monkeypatch.setattr(crowdsec_routes, "redis_client", lambda: FakeRedis())

    # The host tables use Postgres-only types this SQLite database cannot hold;
    # the listing itself is tested against Postgres in test_crowdsec_waf_hosts_pg.
    async def _no_hosts(db):
        return []

    monkeypatch.setattr(crowdsec_routes, "_protected_hosts", _no_hosts)
    return calls


async def test_waf_status_defaults(db_client: AsyncClient, admin_token: str, sent) -> None:
    body = (await db_client.get(WAF, headers=_auth(admin_token))).json()
    assert body["max_body_mb"] == 10
    assert body["body_exceeded_action"] == "partial"
    assert body["run"] is None
    assert body["running"] is False
    assert body["appsec_configured"] is True
    assert body["protected_hosts"] == []


async def test_appsec_not_configured_when_the_url_is_blank(
    db_client: AsyncClient, admin_token: str, sent, monkeypatch
) -> None:
    monkeypatch.setattr(crowdsec_routes.settings, "crowdsec_appsec_url", "  ")
    body = (await db_client.get(WAF, headers=_auth(admin_token))).json()
    assert body["appsec_configured"] is False


async def test_save_stores_bytes_and_queues_the_apply(
    db_client: AsyncClient, admin_token: str, sent, session_factory
) -> None:
    resp = await db_client.patch(
        SAVE,
        headers=_auth(admin_token),
        json={"max_body_mb": 2048, "body_exceeded_action": "allow"},
    )

    assert resp.status_code == 202, resp.text
    assert resp.json()["crowdsec_appsec_max_body_mb"] == 2048
    assert resp.json()["crowdsec_appsec_body_action"] == "allow"
    assert [name for name, _ in sent] == ["app.tasks.crowdsec.apply_waf_settings"]
    async with session_factory() as db:
        row = (await db.execute(select(InstanceSettings))).scalar_one()
    assert row.crowdsec_appsec_max_body_bytes == 2048 * 1024 * 1024


@pytest.mark.parametrize("mb", [0, 2049, -5])
async def test_save_refuses_out_of_range(
    db_client: AsyncClient, admin_token: str, sent, mb: int
) -> None:
    resp = await db_client.patch(
        SAVE, headers=_auth(admin_token), json={"max_body_mb": mb, "body_exceeded_action": "drop"}
    )
    assert resp.status_code == 422
    assert sent == []


async def test_save_refuses_an_unknown_action(
    db_client: AsyncClient, admin_token: str, sent
) -> None:
    resp = await db_client.patch(
        SAVE, headers=_auth(admin_token), json={"max_body_mb": 10, "body_exceeded_action": "skip"}
    )
    assert resp.status_code == 422


async def test_save_is_409_while_an_apply_runs(
    db_client: AsyncClient, admin_token: str, sent, monkeypatch, session_factory
) -> None:
    """A CAPI toggle mid-flight would race the restart."""
    monkeypatch.setattr(
        crowdsec_routes, "redis_client", lambda: FakeRedis({"megoopm:crowdsec:capi-apply"})
    )

    resp = await db_client.patch(
        SAVE, headers=_auth(admin_token), json={"max_body_mb": 100, "body_exceeded_action": "drop"}
    )

    assert resp.status_code == 409
    assert sent == []
    async with session_factory() as db:
        row = (await db.execute(select(InstanceSettings))).scalar_one_or_none()
    assert row is None or row.crowdsec_appsec_max_body_bytes == 10 * 1024 * 1024


async def test_save_is_409_when_reloads_are_not_configured(
    db_client: AsyncClient, admin_token: str, sent, monkeypatch, session_factory
) -> None:
    """Saved anyway, like the blocklist switch, so the choice is not lost."""
    monkeypatch.setattr(crowdsec_routes.settings, "ha_enabled", True)
    monkeypatch.setattr(crowdsec_routes.settings, "crowdsec_control_node_id", None)

    resp = await db_client.patch(
        SAVE, headers=_auth(admin_token), json={"max_body_mb": 100, "body_exceeded_action": "drop"}
    )

    assert resp.status_code == 409
    assert "CROWDSEC_CONTROL_NODE_ID" in resp.json()["detail"]
    async with session_factory() as db:
        row = (await db.execute(select(InstanceSettings))).scalar_one()
    assert row.crowdsec_appsec_max_body_bytes == 100 * 1024 * 1024


async def test_waf_routes_are_admin_only(db_client: AsyncClient, member_token: str, sent) -> None:
    assert (await db_client.get(WAF, headers=_auth(member_token))).status_code == 403
    resp = await db_client.patch(
        SAVE, headers=_auth(member_token), json={"max_body_mb": 10, "body_exceeded_action": "drop"}
    )
    assert resp.status_code == 403
