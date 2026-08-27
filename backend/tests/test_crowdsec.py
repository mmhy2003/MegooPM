"""Tests for the CrowdSec LAPI integration (MEG-22).

Two layers, neither needing a running CrowdSec:

* the :class:`CrowdSecClient` against an ``httpx.MockTransport`` (auth headers,
  JSON mapping, error translation, the manual-decision payload); and
* the admin-gated API routes, with the client dependency overridden onto a
  mock-transport client (RBAC, not-configured → 503, list/create round-trips).
"""

from __future__ import annotations

import json

import httpx
import pytest
from app.core.config import Settings
from app.main import app
from app.schemas.crowdsec import DecisionCreate
from app.services.crowdsec import (
    CrowdSecClient,
    CrowdSecNotConfigured,
    get_crowdsec_client,
)
from app.services.crowdsec.client import CrowdSecError
from httpx import AsyncClient

# --- fixtures / helpers ----------------------------------------------------


def _settings(**over: object) -> Settings:
    base = {
        "crowdsec_lapi_url": "http://crowdsec.test:8080",
        "crowdsec_lapi_key": "bouncer-key",
        "crowdsec_machine_id": "megoopm",
        "crowdsec_machine_password": "secret",
    }
    base.update(over)
    return Settings(**base)


def _client(handler, **over: object) -> CrowdSecClient:
    return CrowdSecClient(_settings(**over), transport=httpx.MockTransport(handler))


# --- client: read paths ----------------------------------------------------


async def test_list_decisions_sends_bouncer_key_and_maps_json() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["key"] = request.headers.get("X-Api-Key")
        return httpx.Response(
            200,
            json=[
                {"id": 1, "type": "ban", "scope": "Ip", "value": "1.2.3.4", "duration": "3h"}
            ],
        )

    async with _client(handler) as client:
        decisions = await client.list_decisions()

    assert seen == {"path": "/v1/decisions", "key": "bouncer-key"}
    assert len(decisions) == 1
    assert decisions[0].value == "1.2.3.4"
    assert decisions[0].type == "ban"


async def test_list_decisions_handles_null_body() -> None:
    # LAPI returns literal ``null`` (not ``[]``) when there are no decisions.
    async with _client(lambda r: httpx.Response(200, json=None)) as client:
        assert await client.list_decisions() == []


async def test_list_decisions_requires_some_credential() -> None:
    # With neither a bouncer key nor machine creds, the read path is unconfigured.
    async with _client(
        lambda r: httpx.Response(200),
        crowdsec_lapi_key=None,
        crowdsec_machine_id=None,
        crowdsec_machine_password=None,
    ) as client:
        with pytest.raises(CrowdSecNotConfigured):
            await client.list_decisions()


async def test_list_decisions_falls_back_to_machine_token() -> None:
    # No bouncer key, but machine creds present: read decisions via the JWT.
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/v1/watchers/login":
            return httpx.Response(200, json={"token": "jwt-xyz"})
        assert request.headers.get("Authorization") == "Bearer jwt-xyz"
        return httpx.Response(
            200, json=[{"type": "ban", "scope": "Ip", "value": "1.1.1.1", "duration": "1h"}]
        )

    async with _client(handler, crowdsec_lapi_key=None) as client:
        decisions = await client.list_decisions()

    assert calls == ["POST /v1/watchers/login", "GET /v1/decisions"]
    assert decisions[0].value == "1.1.1.1"


async def test_list_alerts_logs_in_and_uses_bearer_token() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/v1/watchers/login":
            body = json.loads(request.content)
            assert body == {"machine_id": "megoopm", "password": "secret"}
            return httpx.Response(200, json={"token": "jwt-123", "expire": "later"})
        assert request.headers.get("Authorization") == "Bearer jwt-123"
        return httpx.Response(200, json=[{"id": 9, "scenario": "crowdsecurity/http-probing"}])

    async with _client(handler) as client:
        alerts = await client.list_alerts(limit=10)

    assert calls == ["POST /v1/watchers/login", "GET /v1/alerts"]
    assert alerts[0].scenario == "crowdsecurity/http-probing"


async def test_list_alerts_coerces_null_decisions() -> None:
    # LAPI sends ``decisions: null`` (not ``[]``) for every decision-less alert
    # — notably all AppSec/WAF detections (``crowdsecurity/vpatch-*``). Mixing a
    # decision-bearing alert with a null-decision one mirrors the live shape
    # from MEG-29 and must not 500 the read path (regression: MEG-39).
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/watchers/login":
            return httpx.Response(200, json={"token": "jwt"})
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "scenario": "megoopm/manual-ban",
                    "decisions": [
                        {"type": "ban", "scope": "Ip", "value": "1.2.3.4", "duration": "4h"}
                    ],
                },
                {
                    "id": 2,
                    "scenario": "crowdsecurity/vpatch-env-access",
                    "decisions": None,
                },
            ],
        )

    async with _client(handler) as client:
        alerts = await client.list_alerts()

    assert len(alerts) == 2
    assert alerts[0].decisions[0].value == "1.2.3.4"
    # The null-decision AppSec alert normalizes to an empty list, not a 500.
    assert alerts[1].scenario == "crowdsecurity/vpatch-env-access"
    assert alerts[1].decisions == []


# --- client: write path ----------------------------------------------------


async def test_add_decision_posts_alert_with_decision() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/watchers/login":
            return httpx.Response(200, json={"token": "jwt"})
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=["42"])

    async with _client(handler) as client:
        decision = await client.add_decision(
            DecisionCreate(scope="Ip", value="9.9.9.9", type="ban", duration="6h", reason="spam")
        )

    alert = captured["body"][0]
    assert alert["source"]["value"] == "9.9.9.9"
    assert alert["message"] == "spam"
    dec = alert["decisions"][0]
    assert dec == {
        "origin": "megoopm",
        "type": "ban",
        "scope": "Ip",
        "value": "9.9.9.9",
        "duration": "6h",
        "scenario": "megoopm/manual-ban",
    }
    # The echoed decision mirrors what we asked LAPI to enforce.
    assert decision.value == "9.9.9.9"
    assert decision.origin == "megoopm"


async def test_add_decision_requires_machine_credentials() -> None:
    async with _client(
        lambda r: httpx.Response(200), crowdsec_machine_id=None
    ) as client:
        with pytest.raises(CrowdSecNotConfigured):
            await client.add_decision(DecisionCreate(value="1.1.1.1"))


async def test_http_error_is_translated() -> None:
    async with _client(lambda r: httpx.Response(500, text="boom")) as client:
        with pytest.raises(CrowdSecError) as exc:
            await client.list_decisions()
    assert exc.value.status_code == 500


# --- API routes ------------------------------------------------------------


@pytest.fixture
def override_crowdsec():
    """Install a mock-transport LAPI client as the route dependency.

    The same client instance is reused across requests within a test (so tests
    that paginate over several requests don't hit a closed client); all clients
    are closed once at fixture teardown.
    """
    opened: list[CrowdSecClient] = []

    def _install(handler, **over: object):
        client = _client(handler, **over)
        opened.append(client)

        async def _dep():
            yield client

        app.dependency_overrides[get_crowdsec_client] = _dep
        return client

    yield _install
    app.dependency_overrides.pop(get_crowdsec_client, None)


async def test_decisions_requires_authentication(db_client: AsyncClient) -> None:
    resp = await db_client.get("/api/v1/crowdsec/decisions")
    assert resp.status_code == 401


async def test_decisions_forbidden_for_non_admin(
    db_client: AsyncClient, member_token: str, override_crowdsec
) -> None:
    override_crowdsec(lambda r: httpx.Response(200, json=[]))
    resp = await db_client.get(
        "/api/v1/crowdsec/decisions", headers={"Authorization": f"Bearer {member_token}"}
    )
    assert resp.status_code == 403


async def test_decisions_503_when_unconfigured(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    override_crowdsec(
        lambda r: httpx.Response(200),
        crowdsec_lapi_key=None,
        crowdsec_machine_id=None,
        crowdsec_machine_password=None,
    )
    resp = await db_client.get(
        "/api/v1/crowdsec/decisions", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 503


async def test_admin_lists_decisions(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    override_crowdsec(
        lambda r: httpx.Response(
            200, json=[{"type": "ban", "scope": "Ip", "value": "5.5.5.5", "duration": "1h"}]
        )
    )
    resp = await db_client.get(
        "/api/v1/crowdsec/decisions", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["value"] == "5.5.5.5"


async def test_admin_pushes_manual_decision_and_audits(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/watchers/login":
            return httpx.Response(200, json={"token": "jwt"})
        return httpx.Response(201, json=["1"])

    override_crowdsec(handler)
    resp = await db_client.post(
        "/api/v1/crowdsec/decisions",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"scope": "Ip", "value": "8.8.8.8", "type": "ban", "duration": "2h"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["value"] == "8.8.8.8"

    # The manual ban is recorded in the audit trail.
    audit = await db_client.get(
        "/api/v1/audit-log?object_type=crowdsec_decision",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert audit.status_code == 200
    entries = audit.json()["items"]
    assert entries and entries[0]["meta"]["value"] == "8.8.8.8"


async def test_decisions_paginated_and_hide_community_by_default(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    # Two local + one community (origin=lists) decision. Default view hides the
    # community one; page_size=1 returns a single item with the filtered total.
    decisions = [
        {"origin": "megoopm", "type": "ban", "scope": "Ip", "value": "1.1.1.1", "duration": "1h"},
        {"origin": "crowdsec", "type": "ban", "scope": "Ip", "value": "2.2.2.2", "duration": "1h"},
        {"origin": "lists", "type": "ban", "scope": "Ip", "value": "3.3.3.3", "duration": "1h"},
    ]
    override_crowdsec(lambda r: httpx.Response(200, json=decisions))

    hdr = {"Authorization": f"Bearer {admin_token}"}
    resp = await db_client.get("/api/v1/crowdsec/decisions?page=1&page_size=1", headers=hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2  # community record excluded from the count
    assert body["page"] == 1 and body["page_size"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["value"] == "1.1.1.1"

    # Page 2 returns the second local decision; the community one never appears.
    page2 = await db_client.get("/api/v1/crowdsec/decisions?page=2&page_size=1", headers=hdr)
    assert page2.json()["items"][0]["value"] == "2.2.2.2"

    # include_community=true surfaces the blocklist record and bumps the total.
    full = await db_client.get(
        "/api/v1/crowdsec/decisions?include_community=true", headers=hdr
    )
    fbody = full.json()
    assert fbody["total"] == 3
    assert {d["value"] for d in fbody["items"]} == {"1.1.1.1", "2.2.2.2", "3.3.3.3"}


async def test_alerts_hide_community_by_default(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/watchers/login":
            return httpx.Response(200, json={"token": "jwt"})
        return httpx.Response(
            200,
            json=[
                # AppSec detection (no decisions) — always local.
                {"id": 1, "scenario": "crowdsecurity/vpatch-env-access", "decisions": None},
                # Community-sourced alert (CAPI origin) — hidden by default.
                {
                    "id": 2,
                    "scenario": "crowdsecurity/http-probing",
                    "decisions": [
                        {
                            "origin": "CAPI", "type": "ban", "scope": "Ip",
                            "value": "9.9.9.9", "duration": "4h",
                        }
                    ],
                },
            ],
        )

    override_crowdsec(handler)
    hdr = {"Authorization": f"Bearer {admin_token}"}

    default = await db_client.get("/api/v1/crowdsec/alerts", headers=hdr)
    assert default.status_code == 200, default.text
    dbody = default.json()
    assert dbody["total"] == 1
    assert dbody["items"][0]["scenario"] == "crowdsecurity/vpatch-env-access"

    full = await db_client.get("/api/v1/crowdsec/alerts?include_community=true", headers=hdr)
    assert full.json()["total"] == 2


async def test_page_size_over_cap_is_rejected(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    override_crowdsec(lambda r: httpx.Response(200, json=[]))
    resp = await db_client.get(
        "/api/v1/crowdsec/decisions?page_size=1000",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422  # exceeds the 200 max_page_size cap


async def test_admin_lists_alerts_with_null_decisions(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    # Full HTTP read path the Security dashboard (MEG-23) hits. LAPI sends
    # ``decisions: null`` for every AppSec/WAF detection; the route must return
    # 200, not 500 (live-stack regression from MEG-29, backend fix MEG-39).
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/watchers/login":
            return httpx.Response(200, json={"token": "jwt"})
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "scenario": "megoopm/manual-ban",
                    "decisions": [
                        {"type": "ban", "scope": "Ip", "value": "1.2.3.4", "duration": "4h"}
                    ],
                },
                {"id": 2, "scenario": "crowdsecurity/vpatch-env-access", "decisions": None},
            ],
        )

    override_crowdsec(handler)
    resp = await db_client.get(
        "/api/v1/crowdsec/alerts", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    # The null-decision AppSec alert surfaces with an empty decisions list.
    appsec = next(a for a in body["items"] if a["scenario"] == "crowdsecurity/vpatch-env-access")
    assert appsec["decisions"] == []
