"""A member sees the dashboard and their own account, and nothing else.

The matrix test proves the guard on every route by inspection; these prove
the shape of the rule end to end over HTTP, so a dependency that resolves but
does not actually refuse would still be caught.

``/api/v1/custom-pages`` is the subject because it needs no PostgreSQL ARRAY
column and so works against the SQLite ``db_client`` fixture.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_a_member_cannot_list_custom_pages(db_client: AsyncClient, member_token: str) -> None:
    # Inventory is an admin's, reads included. A member who could list pages
    # could list hosts and certificates too, and the sidebar would be hiding
    # what the API hands out.
    resp = await db_client.get("/api/v1/custom-pages", headers=_auth(member_token))
    assert resp.status_code == 403


async def test_a_member_cannot_read_one_custom_page(
    db_client: AsyncClient, admin_token: str, member_token: str
) -> None:
    created = await db_client.post(
        "/api/v1/custom-pages",
        headers=_auth(admin_token),
        json={"name": "Notice", "description": "", "html": "<h1>hi</h1>"},
    )
    assert created.status_code == 201, created.text
    page_id = created.json()["id"]

    resp = await db_client.get(f"/api/v1/custom-pages/{page_id}", headers=_auth(member_token))

    assert resp.status_code == 403


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/v1/custom-pages", {"name": "x", "description": "", "html": "<p>x</p>"}),
        (
            "post",
            "/api/v1/custom-pages/assist",
            {"instruction": "make it blue", "html": "<p>x</p>"},
        ),
    ],
)
async def test_a_member_cannot_write(
    db_client: AsyncClient, member_token: str, method: str, path: str, body: dict
) -> None:
    resp = await getattr(db_client, method)(path, headers=_auth(member_token), json=body)
    assert resp.status_code == 403


async def test_a_member_cannot_read_the_settings(db_client: AsyncClient, member_token: str) -> None:
    # Out of scope by decision: Settings stays admin, API and page both.
    resp = await db_client.get("/api/v1/settings", headers=_auth(member_token))
    assert resp.status_code == 403


async def test_a_member_cannot_list_users(db_client: AsyncClient, member_token: str) -> None:
    resp = await db_client.get("/api/v1/users", headers=_auth(member_token))
    assert resp.status_code == 403


async def test_a_member_may_still_read_their_own_account(
    db_client: AsyncClient, member_token: str
) -> None:
    """The one place a member writes is their own row; reading it must work."""
    resp = await db_client.get("/api/v1/users/me", headers=_auth(member_token))
    assert resp.status_code == 200
    assert resp.json()["role"] == "member"


# What a member keeps — /api/v1/dashboard/* — is pinned by
# tests/test_route_authorization.py rather than asserted here: the dashboard
# summary aggregates across most of the schema, including tables the SQLite
# fixture cannot hold (ARRAY and JSONB columns), so the request raises out of
# the client instead of answering.


async def test_a_member_cannot_change_crowdsec(db_client: AsyncClient, member_token: str) -> None:
    resp = await db_client.post(
        "/api/v1/crowdsec/whitelists",
        headers=_auth(member_token),
        json={"name": "office", "kind": "ip", "value": "10.0.0.1", "reason": ""},
    )
    assert resp.status_code == 403
