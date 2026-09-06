"""A key authenticating an ordinary route — and the ways it must not.

``/custom-pages`` stands in for "an ordinary route": its table is one the
in-memory fixture builds, and the pair GET/POST is member/admin, so the same
endpoint proves both that a key authenticates and that it authorizes nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.api_key import ApiKey
from app.models.user import User
from app.services import api_keys
from httpx import AsyncClient
from sqlalchemy import select

ME = "/api/v1/users/me"
AUTH_ME = "/api/v1/auth/me"
PAGES = "/api/v1/custom-pages"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _key(session_factory, user: User, **over) -> str:
    async with session_factory() as db:
        owner = await db.get(User, user.id)
        _, token = await api_keys.create(
            db, owner, name=over.pop("name", "CI"), expires_at=over.pop("expires_at", None)
        )
        return token


async def test_a_key_authenticates_an_ordinary_route(
    db_client: AsyncClient, admin_user: User, session_factory
) -> None:
    token = await _key(session_factory, admin_user)

    resp = await db_client.get(PAGES, headers=_bearer(token))

    assert resp.status_code == 200, resp.text


async def test_whoami_answers_so_a_script_can_check_its_key(
    db_client: AsyncClient, admin_user: User, session_factory
) -> None:
    token = await _key(session_factory, admin_user)

    assert (await db_client.get(ME, headers=_bearer(token))).json()["email"] == admin_user.email
    assert (await db_client.get(AUTH_ME, headers=_bearer(token))).status_code == 200


async def test_a_garbage_key_is_401(db_client: AsyncClient) -> None:
    resp = await db_client.get(PAGES, headers=_bearer("mgm_not-a-real-key"))

    assert resp.status_code == 401


async def test_a_near_miss_is_the_same_401_as_an_unknown_key(
    db_client: AsyncClient, admin_user: User, session_factory
) -> None:
    # A guess that shares a prefix must not be told it got that far.
    token = await _key(session_factory, admin_user)
    wrong = token[:-1] + ("A" if token[-1] != "A" else "B")

    resp = await db_client.get(PAGES, headers=_bearer(wrong))

    assert resp.status_code == 401
    assert "disabled" not in resp.text.lower()
    assert "expired" not in resp.text.lower()


async def test_a_disabled_key_is_401_and_says_so(
    db_client: AsyncClient, admin_user: User, session_factory
) -> None:
    token = await _key(session_factory, admin_user)
    async with session_factory() as db:
        key = await db.scalar(select(ApiKey))
        await api_keys.set_enabled(db, key, False)

    resp = await db_client.get(PAGES, headers=_bearer(token))

    assert resp.status_code == 401
    # Only said once the digest matched: the caller holds the key already.
    assert "disabled" in resp.text.lower()


async def test_an_expired_key_is_401_and_says_so(
    db_client: AsyncClient, admin_user: User, session_factory
) -> None:
    token = await _key(
        session_factory, admin_user, expires_at=datetime.now(UTC) - timedelta(minutes=1)
    )

    resp = await db_client.get(PAGES, headers=_bearer(token))

    assert resp.status_code == 401
    assert "expired" in resp.text.lower()


async def test_a_deactivated_owner_takes_every_key_with_them(
    db_client: AsyncClient, admin_user: User, session_factory
) -> None:
    # The admin's one lever over someone else's automation, per the design:
    # deactivate the user and their keys stop.
    token = await _key(session_factory, admin_user)
    async with session_factory() as db:
        user = await db.get(User, admin_user.id)
        user.is_active = False
        await db.commit()

    resp = await db_client.get(PAGES, headers=_bearer(token))

    assert resp.status_code == 401


async def test_a_member_key_grants_nothing_the_member_lacks(
    db_client: AsyncClient, member_user: User, session_factory
) -> None:
    # Key authentication is authentication, never authorization.
    token = await _key(session_factory, member_user)

    read = await db_client.get(PAGES, headers=_bearer(token))
    write = await db_client.post(
        PAGES, headers=_bearer(token), json={"name": "nope", "html": "<h1>no</h1>"}
    )

    assert read.status_code == 200
    assert write.status_code == 403


async def test_using_a_key_records_when(
    db_client: AsyncClient, admin_user: User, session_factory
) -> None:
    token = await _key(session_factory, admin_user)
    async with session_factory() as db:
        assert (await db.scalar(select(ApiKey))).last_used_at is None

    await db_client.get(PAGES, headers=_bearer(token))

    async with session_factory() as db:
        assert (await db.scalar(select(ApiKey))).last_used_at is not None


async def test_a_session_token_still_works(db_client: AsyncClient, admin_token: str) -> None:
    # The branch must not have cost the path everything else uses.
    assert (await db_client.get(PAGES, headers=_bearer(admin_token))).status_code == 200
