"""RBAC tests for the user-management endpoints.

Verifies that admin-only actions are denied to limited (``member``) users with
403, rejected without auth with 401, and permitted for admins.
"""

from __future__ import annotations

from httpx import AsyncClient

USERS = "/api/v1/users"
USERS_ME = "/api/v1/users/me"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_list_users_requires_authentication(db_client: AsyncClient) -> None:
    resp = await db_client.get(USERS)
    assert resp.status_code == 401


async def test_list_users_denied_to_member(db_client: AsyncClient, member_token: str) -> None:
    resp = await db_client.get(USERS, headers=_auth(member_token))
    assert resp.status_code == 403


async def test_list_users_allowed_for_admin(db_client: AsyncClient, admin_token: str) -> None:
    resp = await db_client.get(USERS, headers=_auth(admin_token))
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list)
    assert len(resp.json()) >= 1


async def test_create_user_denied_to_member(db_client: AsyncClient, member_token: str) -> None:
    resp = await db_client.post(
        USERS,
        headers=_auth(member_token),
        json={"email": "new@example.com", "password": "brandnew123"},
    )
    assert resp.status_code == 403


async def test_admin_can_create_user(db_client: AsyncClient, admin_token: str) -> None:
    resp = await db_client.post(
        USERS,
        headers=_auth(admin_token),
        json={
            "email": "new@example.com",
            "password": "brandnew123",
            "full_name": "New User",
            "role": "member",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "new@example.com"
    assert body["role"] == "member"
    assert "hashed_password" not in body

    # The created user can log in.
    login = await db_client.post(
        "/api/v1/auth/login",
        json={"email": "new@example.com", "password": "brandnew123"},
    )
    assert login.status_code == 200, login.text


async def test_admin_can_create_admin_user(db_client: AsyncClient, admin_token: str) -> None:
    resp = await db_client.post(
        USERS,
        headers=_auth(admin_token),
        json={
            "email": "admin2@example.com",
            "password": "anotheradmin1",
            "role": "admin",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["role"] == "admin"


async def test_create_duplicate_email_conflicts(
    db_client: AsyncClient, admin_token: str, admin_user
) -> None:
    resp = await db_client.post(
        USERS,
        headers=_auth(admin_token),
        json={"email": admin_user.email, "password": "irrelevant1"},
    )
    assert resp.status_code == 409


async def test_users_me_returns_caller(db_client: AsyncClient, member_token: str) -> None:
    resp = await db_client.get(USERS_ME, headers=_auth(member_token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == "member@example.com"
    assert resp.json()["role"] == "member"
