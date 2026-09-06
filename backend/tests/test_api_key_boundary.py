"""Keys manage infrastructure, not people.

The route set under test is derived from the running app, not listed here. A new
``/users`` or ``/auth`` route is therefore covered the day it is added, which is
the only way a boundary like this stays true — and it is the same reason
``test_route_authorization.py`` asks the app rather than a list. The two files
are deliberately coupled: they describe the same thing from two angles.
"""

from __future__ import annotations

import pytest
from app.main import app
from app.models.user import User
from app.services import api_keys
from httpx import AsyncClient

from tests.test_route_authorization import iter_routes, route_guard

#: The two "whoami" reads. A script must be able to check that its key works.
ALLOWED = {("GET", "/api/v1/users/me"), ("GET", "/api/v1/auth/me")}


def account_routes() -> list[tuple[str, str]]:
    """Every authenticated route under /auth and /users, bar the whoami reads."""
    found = []
    for method, path, route in iter_routes(app):
        if not (path.startswith("/api/v1/users") or path.startswith("/api/v1/auth")):
            continue
        if (method, path) in ALLOWED:
            continue
        # Unauthenticated routes — login, refresh, reset-password — have nothing
        # to refuse: they never look at the Authorization header.
        if route_guard(route) == "public":
            continue
        found.append((method, path))
    return sorted(found)


async def _key(session_factory, user: User) -> str:
    async with session_factory() as db:
        owner = await db.get(User, user.id)
        _, token = await api_keys.create(db, owner, name="CI", expires_at=None)
        return token


@pytest.mark.parametrize(("method", "path"), account_routes())
async def test_a_key_cannot_reach_an_account_route(
    method: str, path: str, db_client: AsyncClient, admin_user: User, session_factory
) -> None:
    token = await _key(session_factory, admin_user)
    url = (
        path.replace("{user_id}", str(admin_user.id))
        .replace("{passkey_id}", "1")
        .replace("{key_id}", "1")
    )

    resp = await db_client.request(
        method, url, headers={"Authorization": f"Bearer {token}"}, json={}
    )

    # 403, not 401: the caller authenticated fine and is not permitted. A 401
    # would send a script into a pointless re-authentication loop.
    assert resp.status_code == 403, f"{method} {path} answered {resp.status_code}"


async def test_the_set_under_test_is_not_empty() -> None:
    """A filter bug that matched nothing would make every case above vacuous."""
    assert len(account_routes()) > 10


async def test_the_two_whoami_reads_stay_open(
    db_client: AsyncClient, admin_user: User, session_factory
) -> None:
    token = await _key(session_factory, admin_user)
    headers = {"Authorization": f"Bearer {token}"}

    assert (await db_client.get("/api/v1/users/me", headers=headers)).status_code == 200
    assert (await db_client.get("/api/v1/auth/me", headers=headers)).status_code == 200


async def test_a_browser_session_still_reaches_them(
    db_client: AsyncClient, admin_token: str
) -> None:
    # The boundary must refuse keys, not people.
    resp = await db_client.patch(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"full_name": "Renamed"},
    )

    assert resp.status_code == 200, resp.text


async def test_the_refusal_says_what_to_do_instead(
    db_client: AsyncClient, admin_user: User, session_factory
) -> None:
    # A bare 403 on a route that works in a browser reads as a bug.
    token = await _key(session_factory, admin_user)

    resp = await db_client.patch(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"full_name": "Renamed"},
    )

    assert "API key" in resp.text
