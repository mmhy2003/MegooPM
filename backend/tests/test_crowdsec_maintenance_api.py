"""The Updates tab's routes. SQLite-backed; Redis and the broker are faked."""

from __future__ import annotations

import pytest
from app.api.routes import crowdsec as crowdsec_routes
from app.models.crowdsec_job_run import CrowdSecJobRun
from app.models.enums import CrowdSecJobKind, CrowdSecJobTrigger
from httpx import AsyncClient

SETTINGS = "/api/v1/settings"
MAINT = "/api/v1/crowdsec/maintenance"
UPDATE = "/api/v1/crowdsec/hub/update"


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
    return calls


# --- settings --------------------------------------------------------------------


async def test_settings_expose_the_defaults(db_client: AsyncClient, admin_token: str, sent) -> None:
    body = (await db_client.get(SETTINGS, headers=_auth(admin_token))).json()
    assert body["crowdsec_hub_auto_update"] is True
    assert body["crowdsec_hub_update_frequency"] == "daily"
    assert body["crowdsec_hub_update_weekday"] == 6
    assert body["crowdsec_hub_update_hour_utc"] == 3
    assert body["crowdsec_capi_enabled"] is False


async def test_patch_hub_schedule(db_client: AsyncClient, admin_token: str, sent) -> None:
    resp = await db_client.patch(
        f"{SETTINGS}/crowdsec-hub",
        headers=_auth(admin_token),
        json={"auto_update": True, "frequency": "weekly", "weekday": 2, "hour_utc": 22},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["crowdsec_hub_update_frequency"] == "weekly"
    assert resp.json()["crowdsec_hub_update_weekday"] == 2
    assert resp.json()["crowdsec_hub_update_hour_utc"] == 22


@pytest.mark.parametrize(
    "payload",
    [
        {"auto_update": True, "frequency": "daily", "weekday": 7, "hour_utc": 3},
        {"auto_update": True, "frequency": "daily", "weekday": 0, "hour_utc": 24},
        {"auto_update": True, "frequency": "hourly", "weekday": 0, "hour_utc": 3},
    ],
)
async def test_patch_hub_schedule_validates(
    db_client: AsyncClient, admin_token: str, sent, payload
) -> None:
    resp = await db_client.patch(
        f"{SETTINGS}/crowdsec-hub", headers=_auth(admin_token), json=payload
    )
    assert resp.status_code == 422


async def test_patch_capi_saves_and_enqueues(
    db_client: AsyncClient, admin_token: str, sent
) -> None:
    resp = await db_client.patch(
        f"{SETTINGS}/crowdsec-capi", headers=_auth(admin_token), json={"enabled": True}
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["crowdsec_capi_enabled"] is True
    assert sent == [("app.tasks.crowdsec.apply_capi", {})]


async def test_settings_patches_are_admin_only(
    db_client: AsyncClient, member_token: str, sent
) -> None:
    resp = await db_client.patch(
        f"{SETTINGS}/crowdsec-capi", headers=_auth(member_token), json={"enabled": True}
    )
    assert resp.status_code == 403


# --- maintenance status + update now ----------------------------------------------


async def test_maintenance_is_empty_at_first(
    db_client: AsyncClient, admin_token: str, sent
) -> None:
    body = (await db_client.get(MAINT, headers=_auth(admin_token))).json()
    assert body == {
        "hub": None,
        "capi": None,
        "reload_configured": True,
        "running": {"hub": False, "capi": False},
    }


async def test_maintenance_reports_the_last_runs(
    db_client: AsyncClient, admin_token: str, sent, session_factory
) -> None:
    async with session_factory() as db:
        db.add(
            CrowdSecJobRun(
                kind=CrowdSecJobKind.hub_update,
                ok=True,
                trigger=CrowdSecJobTrigger.scheduled,
                restarted=True,
                detail={
                    "updated": ["collections:crowdsecurity/nginx"],
                    "agent_version": "v1.6.4",
                    "latest_agent_version": "v1.8.0",
                },
            )
        )
        await db.commit()
    body = (await db_client.get(MAINT, headers=_auth(admin_token))).json()
    assert body["hub"]["ok"] is True
    assert body["hub"]["detail"]["latest_agent_version"] == "v1.8.0"
    assert body["hub"]["trigger"] == "scheduled"
    assert body["capi"] is None


async def test_maintenance_says_when_a_job_is_running(
    db_client: AsyncClient, admin_token: str, sent, monkeypatch
) -> None:
    monkeypatch.setattr(
        crowdsec_routes, "redis_client", lambda: FakeRedis({"megoopm:crowdsec:hub-update"})
    )
    body = (await db_client.get(MAINT, headers=_auth(admin_token))).json()
    assert body["running"] == {"hub": True, "capi": False}


async def test_update_now_enqueues_a_manual_run(
    db_client: AsyncClient, admin_token: str, sent
) -> None:
    resp = await db_client.post(UPDATE, headers=_auth(admin_token))
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"queued": True}
    assert sent == [("app.tasks.crowdsec.update_hub", {"kwargs": {"trigger": "manual"}})]


async def test_update_now_is_409_while_running(
    db_client: AsyncClient, admin_token: str, sent, monkeypatch
) -> None:
    monkeypatch.setattr(
        crowdsec_routes, "redis_client", lambda: FakeRedis({"megoopm:crowdsec:hub-update"})
    )
    resp = await db_client.post(UPDATE, headers=_auth(admin_token))
    assert resp.status_code == 409
    assert resp.json()["detail"] == "An update is already running."


async def test_update_now_is_409_when_reloads_are_not_configured(
    db_client: AsyncClient, admin_token: str, sent, monkeypatch
) -> None:
    monkeypatch.setattr(crowdsec_routes.settings, "ha_enabled", True)
    monkeypatch.setattr(crowdsec_routes.settings, "crowdsec_control_node_id", None)
    resp = await db_client.post(UPDATE, headers=_auth(admin_token))
    assert resp.status_code == 409
    assert "CROWDSEC_CONTROL_NODE_ID" in resp.json()["detail"]
    assert sent == []


async def test_update_now_is_admin_only(db_client: AsyncClient, member_token: str, sent) -> None:
    assert (await db_client.post(UPDATE, headers=_auth(member_token))).status_code == 403
