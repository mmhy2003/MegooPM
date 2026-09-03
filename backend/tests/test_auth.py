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


# --- token_version: a password change ends existing sessions ---------------


async def _login(db_client: AsyncClient, email: str, password: str) -> dict:
    resp = await db_client.post(LOGIN, json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_refresh_is_refused_after_the_password_changes(
    db_client: AsyncClient, admin_user: User, session_factory
) -> None:
    # The scenario this exists for: someone resets their password because they
    # believe they are compromised. The attacker's refresh token must die.
    tokens = await _login(db_client, admin_user.email, "adminpass123")

    from app.services import user as user_service

    async with session_factory() as session:
        user = await user_service.get_by_id(session, admin_user.id)
        await user_service.set_password(session, user, "newpass12345")

    resp = await db_client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401


async def test_refresh_still_works_when_nothing_changed(
    db_client: AsyncClient, admin_user: User
) -> None:
    tokens = await _login(db_client, admin_user.email, "adminpass123")
    resp = await db_client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 200, resp.text


async def test_self_service_change_ends_other_sessions(
    db_client: AsyncClient, admin_user: User
) -> None:
    first = await _login(db_client, admin_user.email, "adminpass123")
    second = await _login(db_client, admin_user.email, "adminpass123")

    resp = await db_client.put(
        "/api/v1/users/me/password",
        headers={"Authorization": f"Bearer {second['access_token']}"},
        json={"new_password": "newpass12345"},
    )
    assert resp.status_code == 204, resp.text

    # The *other* session's refresh is dead.
    resp = await db_client.post(REFRESH, json={"refresh_token": first["refresh_token"]})
    assert resp.status_code == 401


async def test_admin_reset_ends_the_target_users_sessions(
    db_client: AsyncClient, admin_user: User, member_user: User, admin_token: str
) -> None:
    member = await _login(db_client, member_user.email, "memberpass123")

    resp = await db_client.put(
        f"/api/v1/users/{member_user.id}/password",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"password": "newpass12345"},
    )
    assert resp.status_code == 204, resp.text

    resp = await db_client.post(REFRESH, json={"refresh_token": member["refresh_token"]})
    assert resp.status_code == 401


async def test_deactivation_ends_sessions(
    db_client: AsyncClient, admin_user: User, member_user: User, admin_token: str
) -> None:
    # Already true before token_version existed: refresh refuses an inactive
    # user outright. Kept as a guard so the guarantee is stated, not assumed.
    member = await _login(db_client, member_user.email, "memberpass123")

    resp = await db_client.patch(
        f"/api/v1/users/{member_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"is_active": False},
    )
    assert resp.status_code == 200, resp.text

    resp = await db_client.post(REFRESH, json={"refresh_token": member["refresh_token"]})
    assert resp.status_code == 401


async def test_access_token_is_not_checked_against_the_version(
    db_client: AsyncClient, admin_user: User, session_factory
) -> None:
    # Deliberate: a database read on every authenticated request is not worth
    # it for a token that lives minutes. The spec records this as a known limit.
    tokens = await _login(db_client, admin_user.email, "adminpass123")

    from app.services import user as user_service

    async with session_factory() as session:
        user = await user_service.get_by_id(session, admin_user.id)
        await user_service.set_password(session, user, "newpass12345")

    resp = await db_client.get(ME, headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert resp.status_code == 200
