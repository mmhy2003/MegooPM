"""The Error pages settings card's two routes."""

from __future__ import annotations

import pytest
from app.api.routes import _config_writes as config_writes
from app.models.error_page import ERROR_CODES
from app.schemas.tasks import TaskEnqueued
from httpx import AsyncClient

URL = "/api/v1/settings/error-pages"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _no_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    """A write re-renders nginx; these tests have no broker to enqueue on."""
    monkeypatch.setattr(
        config_writes,
        "enqueue_nginx_reload",
        lambda: TaskEnqueued(task_id="test-reload-task", status="PENDING"),
    )


async def _make_page(db_client: AsyncClient, token: str, name: str = "Oops") -> int:
    resp = await db_client.post(
        "/api/v1/custom-pages",
        headers=_auth(token),
        json={"name": name, "description": "", "html": "<h1>mine</h1>"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_all_eight_codes_come_back_defaulted(
    db_client: AsyncClient, admin_token: str
) -> None:
    # The card renders rows from this, so an unconfigured instance must still
    # answer with the full set rather than an empty list.
    body = (await db_client.get(URL, headers=_auth(admin_token))).json()
    assert [row["code"] for row in body] == list(ERROR_CODES)
    assert {row["mode"] for row in body} == {"default"}
    assert all(row["custom_page_id"] is None for row in body)


async def test_a_whole_set_write_stores_only_what_differs(
    db_client: AsyncClient, admin_token: str
) -> None:
    page_id = await _make_page(db_client, admin_token)
    payload = [{"code": code, "mode": "default", "custom_page_id": None} for code in ERROR_CODES]
    payload[3] = {"code": 404, "mode": "custom_page", "custom_page_id": page_id}

    resp = await db_client.put(URL, headers=_auth(admin_token), json=payload)

    assert resp.status_code == 200, resp.text
    stored = {row["code"]: row for row in resp.json()}
    assert stored[404]["mode"] == "custom_page"
    assert stored[404]["custom_page_id"] == page_id
    assert stored[502]["mode"] == "default"


async def test_setting_a_code_back_to_default_deletes_its_row(
    db_client: AsyncClient, admin_token: str, session_factory
) -> None:
    from app.models.error_page import ErrorPage
    from sqlalchemy import select

    page_id = await _make_page(db_client, admin_token)
    payload = [{"code": c, "mode": "default", "custom_page_id": None} for c in ERROR_CODES]
    payload[3] = {"code": 404, "mode": "custom_page", "custom_page_id": page_id}
    await db_client.put(URL, headers=_auth(admin_token), json=payload)

    payload[3] = {"code": 404, "mode": "default", "custom_page_id": None}
    await db_client.put(URL, headers=_auth(admin_token), json=payload)

    async with session_factory() as db:
        rows = (await db.execute(select(ErrorPage))).scalars().all()
    # Nothing configured means nothing stored.
    assert rows == []


@pytest.mark.parametrize(
    ("row", "fragment"),
    [
        ({"code": 404, "mode": "custom_page", "custom_page_id": None}, "page"),
        ({"code": 404, "mode": "default", "custom_page_id": 1}, "page"),
        ({"code": 418, "mode": "default", "custom_page_id": None}, "418"),
    ],
)
async def test_a_bad_row_is_422(db_client: AsyncClient, admin_token: str, row, fragment) -> None:
    resp = await db_client.put(URL, headers=_auth(admin_token), json=[row])
    assert resp.status_code == 422, resp.text
    assert fragment in resp.text


async def test_a_missing_page_is_422_naming_the_code(
    db_client: AsyncClient, admin_token: str
) -> None:
    resp = await db_client.put(
        URL,
        headers=_auth(admin_token),
        json=[{"code": 404, "mode": "custom_page", "custom_page_id": 9999}],
    )
    assert resp.status_code == 422
    assert "404" in resp.json()["detail"]


async def test_both_routes_are_admin_only(db_client: AsyncClient, member_token: str) -> None:
    assert (await db_client.get(URL, headers=_auth(member_token))).status_code == 403
    assert (await db_client.put(URL, headers=_auth(member_token), json=[])).status_code == 403


async def test_a_write_is_audited(
    db_client: AsyncClient, admin_token: str, session_factory
) -> None:
    from app.models.audit_log import AuditLog
    from sqlalchemy import select

    page_id = await _make_page(db_client, admin_token)
    await db_client.put(
        URL,
        headers=_auth(admin_token),
        json=[{"code": 404, "mode": "custom_page", "custom_page_id": page_id}],
    )
    async with session_factory() as db:
        rows = (await db.execute(select(AuditLog))).scalars().all()
    assert any(r.object_type == "error_page" for r in rows)
