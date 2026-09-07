"""One alert, inspected — the shape `cscli alerts inspect -d` prints.

The list endpoint deliberately does not carry this: an alert holds tens of
events and each event holds ~17 parsed fields, so returning them for fifty
alerts would bloat every page load of a table that renders none of it. cscli
fetches one alert by id for the same reason.
"""

from __future__ import annotations

import httpx
from app.core.config import Settings
from app.schemas.crowdsec import Alert
from app.services.crowdsec import CrowdSecClient
from httpx import AsyncClient

#: A realistic LAPI payload, trimmed to one event. The field names are taken
#: from the documented `cscli alerts inspect -d` output.
LAPI_ALERT = {
    "id": 176012,
    "uuid": "0061339c-f070-4859-8f2a-66249c709d73",
    "machine_id": "testMachine",
    "simulated": False,
    "remediation": True,
    "scenario": "crowdsecurity/http-crawl-non_statics",
    "message": "Ip 192.168.1.100 performed 'crowdsecurity/http-crawl-non_statics' (44 events)",
    "events_count": 44,
    "created_at": "2026-01-07T15:11:08Z",
    "start_at": "2026-01-07T15:11:05Z",
    "stop_at": "2026-01-07T15:11:07Z",
    "source": {
        "scope": "Ip",
        "value": "192.168.1.100",
        "ip": "192.168.1.100",
        "cn": "US",
        "as_name": "EXAMPLE-AS-BLOCK",
    },
    "decisions": [
        {
            "id": 905003939,
            "origin": "crowdsec",
            "type": "ban",
            "scope": "Ip",
            "value": "192.168.1.100",
            "duration": "23h35m33s",
            "until": "2026-01-08T14:46:41Z",
            "simulated": False,
            "scenario": "crowdsecurity/http-crawl-non_statics",
        }
    ],
    # cscli prints these as the "Context" table.
    "meta": [
        {"key": "method", "value": "GET"},
        {"key": "target_uri", "value": "/lanz.php"},
    ],
    # cscli prints these as the "Events" section, one table each.
    "events": [
        {
            "timestamp": "2026-01-07T15:11:07Z",
            "meta": [
                {"key": "http_path", "value": "/lanz.php"},
                {"key": "http_verb", "value": "GET"},
                {"key": "http_status", "value": "404"},
                {"key": "source_ip", "value": "192.168.1.100"},
            ],
        }
    ],
}


def test_the_alert_schema_carries_what_inspect_prints() -> None:
    alert = Alert.model_validate(LAPI_ALERT)

    assert alert.machine_id == "testMachine"
    assert alert.uuid == "0061339c-f070-4859-8f2a-66249c709d73"
    assert alert.simulated is False
    assert alert.remediation is True
    # The Context table.
    assert [(m.key, m.value) for m in alert.meta] == [
        ("method", "GET"),
        ("target_uri", "/lanz.php"),
    ]
    # The Events section.
    assert len(alert.events) == 1
    assert alert.events[0].timestamp == "2026-01-07T15:11:07Z"
    assert ("http_status", "404") in [(m.key, m.value) for m in alert.events[0].meta]


def test_a_decision_carries_its_expiry() -> None:
    """A duration string alone cannot answer "when does this lift?"."""
    alert = Alert.model_validate(LAPI_ALERT)
    assert alert.decisions[0].until == "2026-01-08T14:46:41Z"
    assert alert.decisions[0].simulated is False


def test_an_alert_without_context_or_events_still_parses() -> None:
    # LAPI sends null for these on an alert that carried neither — every
    # AppSec detection, for one.
    alert = Alert.model_validate({**LAPI_ALERT, "meta": None, "events": None})
    assert alert.meta == []
    assert alert.events == []


def test_a_list_alert_without_the_new_fields_still_parses() -> None:
    """The list path sends a thinner alert; it must not start failing."""
    alert = Alert.model_validate(
        {"id": 1, "scenario": "x", "message": "m", "events_count": 2, "decisions": []}
    )
    assert alert.machine_id is None
    assert alert.meta == []


def _client(handler) -> CrowdSecClient:
    """A LAPI client whose transport is the given handler."""
    settings = Settings(
        crowdsec_lapi_url="http://crowdsec.test:8080",
        crowdsec_lapi_key="bouncer-key",
        crowdsec_machine_id="megoopm",
        crowdsec_machine_password="secret",
    )
    return CrowdSecClient(settings, transport=httpx.MockTransport(handler))


async def test_the_client_fetches_one_alert_by_id() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/watchers/login"):
            return httpx.Response(200, json={"token": "t", "expire": "2099-01-01T00:00:00Z"})
        seen["path"] = request.url.path
        return httpx.Response(200, json=LAPI_ALERT)

    async with _client(handler) as client:
        alert = await client.get_alert(176012)

    assert seen["path"] == "/v1/alerts/176012"
    assert alert.machine_id == "testMachine"
    assert len(alert.events) == 1


# --- the route -----------------------------------------------------------------


def _lapi(response: httpx.Response):
    """A handler that satisfies the machine login, then answers with ``response``.

    Reading alerts is machine-authenticated, so the client logs in first; a
    handler that returns the alert to every request fails that step instead.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/watchers/login":
            return httpx.Response(200, json={"token": "jwt-123", "expire": "later"})
        return response

    return handler


async def test_a_member_cannot_inspect_an_alert(
    db_client: AsyncClient, member_token: str, override_crowdsec
) -> None:
    """The Security page is an admin's, its reads included."""
    override_crowdsec(_lapi(httpx.Response(200, json=LAPI_ALERT)))

    resp = await db_client.get(
        "/api/v1/crowdsec/alerts/176012",
        headers={"Authorization": f"Bearer {member_token}"},
    )

    assert resp.status_code == 403


async def test_an_admin_inspects_an_alert(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    """The happy path: the route carries what `cscli alerts inspect -d` prints."""
    override_crowdsec(_lapi(httpx.Response(200, json=LAPI_ALERT)))

    resp = await db_client.get(
        "/api/v1/crowdsec/alerts/176012",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["machine_id"] == "testMachine"
    assert body["meta"][0]["key"] == "method"
    assert body["events"][0]["meta"][0]["value"] == "/lanz.php"


async def test_an_unknown_alert_is_not_found(
    db_client: AsyncClient, admin_token: str, override_crowdsec
) -> None:
    # LAPI answers 404; the operator should see that, not a 502.
    override_crowdsec(_lapi(httpx.Response(404, text="not found")))

    resp = await db_client.get(
        "/api/v1/crowdsec/alerts/999999",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert resp.status_code == 404


async def test_a_stranger_cannot_inspect_an_alert(db_client: AsyncClient) -> None:
    resp = await db_client.get("/api/v1/crowdsec/alerts/1")
    assert resp.status_code == 401
