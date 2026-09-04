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
            json=[{"id": 1, "type": "ban", "scope": "Ip", "value": "1.2.3.4", "duration": "3h"}],
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


async def test_list_decisions_never_falls_back_to_the_machine_token() -> None:
    """GET /v1/decisions is an API-key endpoint; a machine JWT can only 403.

    This used to fall back to the machine token for deployments holding only
    machine credentials. Against a real LAPI that fallback returns 403 every
    time, so it turned a missing setting into an opaque failure.
    """
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json=[])

    async with _client(handler, crowdsec_lapi_key=None) as client:
        with pytest.raises(CrowdSecNotConfigured, match="bouncer key"):
            await client.list_decisions()
    # It must not even try to log in.
    assert calls == []


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
    async with _client(lambda r: httpx.Response(200), crowdsec_machine_id=None) as client:
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
    full = await db_client.get("/api/v1/crowdsec/decisions?include_community=true", headers=hdr)
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
                            "origin": "CAPI",
                            "type": "ban",
                            "scope": "Ip",
                            "value": "9.9.9.9",
                            "duration": "4h",
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


# --- registration token / health / lazy registration -----------------------


async def test_register_machine_sends_registration_token_when_configured() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(202)  # LAPI answers 202 Accepted on registration

    async with _client(handler) as client:
        await client.register_machine("m1", "pw", registration_token="k" * 32)
        await client.register_machine("m2", "pw")

    assert bodies[0] == {"machine_id": "m1", "password": "pw", "registration_token": "k" * 32}
    assert bodies[1] == {"machine_id": "m2", "password": "pw"}  # key absent without a token


async def test_health_reports_whether_a_machine_is_registered(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    override_crowdsec(
        lambda r: httpx.Response(200, json=[]),
        crowdsec_machine_id=None,
        crowdsec_machine_password=None,
    )
    resp = await db_client.get(
        "/api/v1/crowdsec/health", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["machine_registered"] is False
    assert "machine" in (body["detail"] or "").lower()

    override_crowdsec(lambda r: httpx.Response(200, json=[]))
    resp = await db_client.get(
        "/api/v1/crowdsec/health", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.json()["machine_registered"] is True


async def test_lapi_rejection_is_reported_as_503_not_502(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    """502/504 get swallowed and rewritten by CDNs (Cloudflare), which strips the
    JSON detail and CORS headers — the UI then only sees "Failed to fetch"."""
    override_crowdsec(lambda r: httpx.Response(401, json={"message": "nope"}))
    resp = await db_client.get(
        "/api/v1/crowdsec/alerts", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 503
    assert "401" in resp.json()["detail"]


async def test_client_dependency_ensures_registration(session_factory, monkeypatch) -> None:
    """The request-scoped client triggers (cached, idempotent) registration so a
    stack whose CrowdSec came up after the backend still self-heals."""
    from app.services import crowdsec as crowdsec_pkg
    from app.services.crowdsec import registration

    calls: list[object] = []

    async def fake_ensure(db, *, settings=None):
        calls.append(db)
        return None

    monkeypatch.setattr(registration, "ensure_registered", fake_ensure)
    async with session_factory() as db:
        async for client in crowdsec_pkg.get_crowdsec_client(db=db):
            await client.aclose()
    assert len(calls) == 1


# --- diagnosability and the alert fetch cap (production incident 2026-08-31) --


def test_alert_fetch_cap_is_within_what_lapi_can_serve() -> None:
    """CrowdSec 1.6.4 hangs on GET /v1/alerts with a large limit.

    Measured against a live LAPI holding ~136 alerts: limit=200 returned every
    alert in 0.03s, limit=1000 timed out on 4 of 4 attempts. The high cap
    fetched no extra data — it only triggered the hang, which surfaced as a
    503 on the Security page.
    """
    from app.services.crowdsec.filtering import ALERT_FETCH_CAP

    assert ALERT_FETCH_CAP <= 200


def test_alert_fetch_cap_is_configurable() -> None:
    """A larger install must be able to tune this without a code change."""
    from app.core.config import Settings

    assert Settings(secret_key="x", crowdsec_alert_fetch_cap=500).crowdsec_alert_fetch_cap == 500


@pytest.mark.asyncio
async def test_request_failure_names_the_endpoint_and_cause() -> None:
    """A bare timeout stringifies to "", which told an operator nothing.

    The message must carry the exception type, the method and path, and the
    configured base URL and timeout.
    """
    import httpx
    from app.services.crowdsec.client import CrowdSecClient, CrowdSecError

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/watchers/login":
            return httpx.Response(200, json={"token": "jwt"})
        raise httpx.ReadTimeout("")  # the empty message is the point

    settings = _settings(crowdsec_lapi_url="http://lapi.test:8080", crowdsec_timeout_seconds=5.0)
    client = CrowdSecClient(settings, transport=httpx.MockTransport(_handler))
    try:
        with pytest.raises(CrowdSecError) as err:
            await client.list_alerts()
    finally:
        await client.aclose()

    text = str(err.value)
    assert "ReadTimeout" in text
    assert "/v1/alerts" in text
    assert "http://lapi.test:8080" in text
    assert "5.0" in text


@pytest.mark.asyncio
async def test_reading_decisions_without_a_bouncer_key_says_so() -> None:
    """GET /v1/decisions is bouncer-key-only; a machine JWT can only ever 403.

    Falling back to the machine token produced an opaque 403 instead of naming
    the missing configuration.
    """
    from app.services.crowdsec.client import CrowdSecClient, CrowdSecError

    settings = _settings(crowdsec_lapi_key=None)
    client = CrowdSecClient(settings)
    try:
        with pytest.raises(CrowdSecError, match="bouncer"):
            await client.list_decisions()
    finally:
        await client.aclose()


async def test_decisions_q_filters_before_pagination(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    # The trap this exists for: filtering the *page* instead of the whole set
    # tells an operator "no matches" while the match sits on page 3.
    decisions = [
        {
            "origin": "megoopm",
            "type": "ban",
            "scope": "Ip",
            "value": f"10.0.0.{i}",
            "duration": "1h",
            "scenario": "crowdsecurity/http-probing",
        }
        for i in range(1, 6)
    ]
    decisions.append(
        {
            "origin": "megoopm",
            "type": "ban",
            "scope": "Ip",
            "value": "203.0.113.7",
            "duration": "1h",
            "scenario": "crowdsecurity/ssh-bf",
        }
    )
    override_crowdsec(lambda r: httpx.Response(200, json=decisions))
    hdr = {"Authorization": f"Bearer {admin_token}"}

    # 203.0.113.7 is the sixth record — page 2 with page_size=5, so a filter
    # applied after pagination would find nothing on page 1.
    resp = await db_client.get(
        "/api/v1/crowdsec/decisions?q=203.0.113.7&page=1&page_size=5", headers=hdr
    )
    assert resp.status_code == 200, resp.text
    assert [d["value"] for d in resp.json()["items"]] == ["203.0.113.7"]


async def test_decisions_q_total_is_the_filtered_count(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    # ``total`` drives the pager. Returning the unfiltered count offers pages
    # that no longer exist, which reads as data loss.
    decisions = [
        {"origin": "megoopm", "type": "ban", "scope": "Ip", "value": "10.0.0.1", "duration": "1h"},
        {"origin": "megoopm", "type": "ban", "scope": "Ip", "value": "10.0.0.2", "duration": "1h"},
        {
            "origin": "megoopm",
            "type": "ban",
            "scope": "Ip",
            "value": "203.0.113.7",
            "duration": "1h",
        },
    ]
    override_crowdsec(lambda r: httpx.Response(200, json=decisions))
    hdr = {"Authorization": f"Bearer {admin_token}"}

    resp = await db_client.get("/api/v1/crowdsec/decisions?q=203.0", headers=hdr)
    assert resp.json()["total"] == 1


async def test_decisions_q_matches_scenario_case_insensitively(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    decisions = [
        {
            "origin": "megoopm",
            "type": "ban",
            "scope": "Ip",
            "value": "10.0.0.1",
            "duration": "1h",
            "scenario": "crowdsecurity/SSH-bf",
        },
        {
            "origin": "megoopm",
            "type": "ban",
            "scope": "Ip",
            "value": "10.0.0.2",
            "duration": "1h",
            "scenario": "crowdsecurity/http-probing",
        },
    ]
    override_crowdsec(lambda r: httpx.Response(200, json=decisions))
    hdr = {"Authorization": f"Bearer {admin_token}"}

    resp = await db_client.get("/api/v1/crowdsec/decisions?q=ssh-BF", headers=hdr)
    assert [d["value"] for d in resp.json()["items"]] == ["10.0.0.1"]


async def test_blank_q_is_not_a_filter(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    # A box the operator selected and hit space in must not empty the table.
    decisions = [
        {"origin": "megoopm", "type": "ban", "scope": "Ip", "value": "10.0.0.1", "duration": "1h"},
    ]
    override_crowdsec(lambda r: httpx.Response(200, json=decisions))
    hdr = {"Authorization": f"Bearer {admin_token}"}

    resp = await db_client.get("/api/v1/crowdsec/decisions?q=%20%20", headers=hdr)
    assert resp.json()["total"] == 1


async def test_alerts_q_matches_source_ip_and_scenario(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/watchers/login":
            return httpx.Response(200, json={"token": "jwt"})
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "scenario": "crowdsecurity/ssh-bf",
                    "decisions": None,
                    "source": {"value": "203.0.113.7", "ip": "203.0.113.7"},
                },
                {
                    "id": 2,
                    "scenario": "crowdsecurity/http-probing",
                    "decisions": None,
                    "source": {"value": "198.51.100.2", "ip": "198.51.100.2"},
                },
            ],
        )

    override_crowdsec(handler)
    hdr = {"Authorization": f"Bearer {admin_token}"}

    by_ip = await db_client.get("/api/v1/crowdsec/alerts?q=203.0.113.7", headers=hdr)
    assert by_ip.status_code == 200, by_ip.text
    assert by_ip.json()["total"] == 1
    assert by_ip.json()["items"][0]["id"] == 1

    by_scenario = await db_client.get("/api/v1/crowdsec/alerts?q=probing", headers=hdr)
    assert by_scenario.json()["total"] == 1
    assert by_scenario.json()["items"][0]["id"] == 2


async def test_alerts_q_survives_an_alert_with_no_source(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    # AppSec detections arrive with no source block; matching must not blow up.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/watchers/login":
            return httpx.Response(200, json={"token": "jwt"})
        return httpx.Response(
            200,
            json=[{"id": 1, "scenario": "crowdsecurity/vpatch-env-access", "decisions": None}],
        )

    override_crowdsec(handler)
    hdr = {"Authorization": f"Bearer {admin_token}"}

    resp = await db_client.get("/api/v1/crowdsec/alerts?q=vpatch", headers=hdr)
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 1


async def test_decisions_carry_a_country(
    db_client: AsyncClient, admin_token: str, override_crowdsec, monkeypatch
) -> None:
    from app.services.crowdsec import geo

    monkeypatch.setattr(geo, "lookup_country", lambda ip: "DE" if ip == "5.5.5.5" else None)
    override_crowdsec(
        lambda r: httpx.Response(
            200,
            json=[
                {"type": "ban", "scope": "Ip", "value": "5.5.5.5", "duration": "1h"},
                {"type": "ban", "scope": "Country", "value": "fr", "duration": "1h"},
                {"type": "ban", "scope": "AS", "value": "AS64496", "duration": "1h"},
            ],
        )
    )
    resp = await db_client.get(
        "/api/v1/crowdsec/decisions", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    assert [d["country"] for d in resp.json()["items"]] == ["DE", "FR", None]


async def test_alerts_get_a_country_when_crowdsec_left_it_blank(
    db_client: AsyncClient, admin_token: str, override_crowdsec, monkeypatch
) -> None:
    from app.services.crowdsec import geo

    monkeypatch.setattr(geo, "lookup_country", lambda ip: "DE" if ip == "5.5.5.5" else None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/watchers/login":
            return httpx.Response(200, json={"token": "jwt"})
        return httpx.Response(
            200,
            json=[
                {"id": 1, "source": {"ip": "5.5.5.5"}, "decisions": []},
                {"id": 2, "source": {"ip": "5.5.5.5", "cn": "XX"}, "decisions": []},
            ],
        )

    override_crowdsec(handler)
    resp = await db_client.get(
        "/api/v1/crowdsec/alerts", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200, resp.text
    assert [a["source"]["cn"] for a in resp.json()["items"]] == ["DE", "XX"]
