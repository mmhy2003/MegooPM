"""Authentication endpoint tests: login, refresh, current-user, and 401 gating."""

from __future__ import annotations

from app.models.user import User
from httpx import AsyncClient

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
ME = "/api/v1/auth/me"


async def test_login_returns_token_pair(db_client: AsyncClient, admin_user: User) -> None:
    resp = await db_client.post(LOGIN, json={"email": admin_user.email, "password": "adminpass123"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"


async def test_login_is_case_insensitive_on_email(db_client: AsyncClient, admin_user: User) -> None:
    resp = await db_client.post(
        LOGIN, json={"email": "ADMIN@example.com", "password": "adminpass123"}
    )
    assert resp.status_code == 200, resp.text


async def test_login_wrong_password_is_401(db_client: AsyncClient, admin_user: User) -> None:
    resp = await db_client.post(LOGIN, json={"email": admin_user.email, "password": "wrong"})
    assert resp.status_code == 401


async def test_login_unknown_email_is_401(db_client: AsyncClient) -> None:
    resp = await db_client.post(LOGIN, json={"email": "nobody@example.com", "password": "whatever"})
    assert resp.status_code == 401


async def test_me_requires_authentication(db_client: AsyncClient) -> None:
    resp = await db_client.get(ME)
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate") == "Bearer"


async def test_me_rejects_garbage_token(db_client: AsyncClient) -> None:
    resp = await db_client.get(ME, headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401


async def test_me_returns_current_user(
    db_client: AsyncClient, admin_token: str, admin_user: User
) -> None:
    resp = await db_client.get(ME, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email"] == admin_user.email
    assert body["role"] == "admin"
    assert "hashed_password" not in body


async def test_refresh_rotates_and_yields_usable_access_token(
    db_client: AsyncClient, admin_user: User
) -> None:
    login = await db_client.post(
        LOGIN, json={"email": admin_user.email, "password": "adminpass123"}
    )
    refresh_token = login.json()["refresh_token"]

    refreshed = await db_client.post(REFRESH, json={"refresh_token": refresh_token})
    assert refreshed.status_code == 200, refreshed.text
    new_access = refreshed.json()["access_token"]
    assert refreshed.json()["refresh_token"]

    # The freshly minted access token authenticates against a protected route.
    me = await db_client.get(ME, headers={"Authorization": f"Bearer {new_access}"})
    assert me.status_code == 200


async def test_refresh_rejects_an_access_token(db_client: AsyncClient, admin_token: str) -> None:
    # An access token must not be accepted where a refresh token is required.
    resp = await db_client.post(REFRESH, json={"refresh_token": admin_token})
    assert resp.status_code == 401


async def test_refresh_rejects_garbage(db_client: AsyncClient) -> None:
    resp = await db_client.post(REFRESH, json={"refresh_token": "nonsense"})
    assert resp.status_code == 401


async def test_access_token_cannot_be_used_as_bearer_when_deactivated(
    db_client: AsyncClient, member_token: str, session_factory
) -> None:
    # Deactivating the user invalidates their still-unexpired access token.
    from app.models.user import User as UserModel
    from sqlalchemy import update

    async with session_factory() as session:
        await session.execute(
            update(UserModel).where(UserModel.email == "member@example.com").values(is_active=False)
        )
        await session.commit()

    resp = await db_client.get(ME, headers={"Authorization": f"Bearer {member_token}"})
    assert resp.status_code == 401
