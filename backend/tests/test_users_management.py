"""Route tests for admin user management: update, password reset, delete, audit."""

from __future__ import annotations

from app.models.audit_log import AuditLog
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

USERS = "/api/v1/users"
ME = "/api/v1/users/me"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(client: AsyncClient, email: str, password: str) -> str:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _create(client: AsyncClient, admin_token: str, email: str, role: str = "member") -> dict:
    resp = await client.post(
        USERS,
        headers=_auth(admin_token),
        json={"email": email, "password": "password123", "role": role, "full_name": ""},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _audit_rows(session_factory: async_sessionmaker) -> list[AuditLog]:
    async with session_factory() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.object_type == "user").order_by(AuditLog.id)
        )
        return list(result.scalars().all())


# --- PATCH /users/{id} ------------------------------------------------------


async def test_admin_updates_name_role_and_active(db_client: AsyncClient, admin_token: str) -> None:
    target = await _create(db_client, admin_token, "t@example.com")
    resp = await db_client.patch(
        f"{USERS}/{target['id']}",
        headers=_auth(admin_token),
        json={"full_name": "Tee", "role": "admin", "is_active": False},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["full_name"], body["role"], body["is_active"]) == ("Tee", "admin", False)


async def test_update_denied_to_member_and_unauthenticated(
    db_client: AsyncClient, admin_token: str, member_token: str
) -> None:
    target = await _create(db_client, admin_token, "t@example.com")
    anon = await db_client.patch(f"{USERS}/{target['id']}", json={"full_name": "x"})
    assert anon.status_code == 401
    resp = await db_client.patch(
        f"{USERS}/{target['id']}", headers=_auth(member_token), json={"full_name": "x"}
    )
    assert resp.status_code == 403


async def test_update_unknown_user_is_404(db_client: AsyncClient, admin_token: str) -> None:
    resp = await db_client.patch(
        f"{USERS}/999999", headers=_auth(admin_token), json={"full_name": "x"}
    )
    assert resp.status_code == 404


async def test_update_rejects_email_change(db_client: AsyncClient, admin_token: str) -> None:
    target = await _create(db_client, admin_token, "t@example.com")
    resp = await db_client.patch(
        f"{USERS}/{target['id']}", headers=_auth(admin_token), json={"email": "new@example.com"}
    )
    assert resp.status_code == 422


async def test_self_role_change_deactivate_and_delete_are_409(
    db_client: AsyncClient, admin_token: str
) -> None:
    me = (await db_client.get(ME, headers=_auth(admin_token))).json()
    # A second admin exists, so only the *self* rule can be what trips.
    await _create(db_client, admin_token, "other@example.com", role="admin")

    r1 = await db_client.patch(
        f"{USERS}/{me['id']}", headers=_auth(admin_token), json={"role": "member"}
    )
    r2 = await db_client.patch(
        f"{USERS}/{me['id']}", headers=_auth(admin_token), json={"is_active": False}
    )
    r3 = await db_client.delete(f"{USERS}/{me['id']}", headers=_auth(admin_token))
    assert (r1.status_code, r2.status_code, r3.status_code) == (409, 409, 409)
    assert "own role" in r1.json()["detail"]


async def test_admin_handover_between_two_admins(
    db_client: AsyncClient, admin_token: str, admin_user
) -> None:
    """With two active admins, either may demote/delete the other; the survivor
    is then protected. (Through the API the last-admin rule can only ever be
    hit as a self-action — any *other* actor must itself be an active admin —
    so the pure "other actor" case is covered in test_user_service.py.)"""
    second = await _create(db_client, admin_token, "second@example.com", role="admin")
    second_token = await _login(db_client, "second@example.com", "password123")

    # Two active admins: `second` may demote the original...
    demote = await db_client.patch(
        f"{USERS}/{admin_user.id}", headers=_auth(second_token), json={"role": "member"}
    )
    assert demote.status_code == 200, demote.text
    # ...after which the original (now a member) is locked out of admin routes.
    assert (await db_client.get(USERS, headers=_auth(admin_token))).status_code == 403

    # `second` promotes the original back, and the original deletes `second`.
    promote = await db_client.patch(
        f"{USERS}/{admin_user.id}", headers=_auth(second_token), json={"role": "admin"}
    )
    assert promote.status_code == 200, promote.text
    gone = await db_client.delete(f"{USERS}/{second['id']}", headers=_auth(admin_token))
    assert gone.status_code == 204

    # The original is now the only active admin and cannot remove itself.
    resp = await db_client.patch(
        f"{USERS}/{admin_user.id}", headers=_auth(admin_token), json={"is_active": False}
    )
    assert resp.status_code == 409
    assert "own account" in resp.json()["detail"]


async def test_update_writes_audit_row_with_diff(
    db_client: AsyncClient, admin_token: str, session_factory: async_sessionmaker
) -> None:
    target = await _create(db_client, admin_token, "t@example.com")
    await db_client.patch(
        f"{USERS}/{target['id']}",
        headers=_auth(admin_token),
        json={"full_name": "Tee", "role": "admin"},
    )
    rows = await _audit_rows(session_factory)
    update = [r for r in rows if r.action == "update"]
    assert len(update) == 1
    assert update[0].actor == "admin@example.com"
    assert update[0].object_id == target["id"]
    assert update[0].meta == {"changes": {"full_name": ["", "Tee"], "role": ["member", "admin"]}}


async def test_active_flip_audits_as_enable_disable(
    db_client: AsyncClient, admin_token: str, session_factory: async_sessionmaker
) -> None:
    target = await _create(db_client, admin_token, "t@example.com")
    await db_client.patch(
        f"{USERS}/{target['id']}", headers=_auth(admin_token), json={"is_active": False}
    )
    await db_client.patch(
        f"{USERS}/{target['id']}", headers=_auth(admin_token), json={"is_active": True}
    )
    actions = [r.action for r in await _audit_rows(session_factory)]
    assert actions == ["create", "disable", "enable"]


async def test_create_writes_audit_row(
    db_client: AsyncClient, admin_token: str, session_factory: async_sessionmaker
) -> None:
    target = await _create(db_client, admin_token, "t@example.com", role="admin")
    rows = await _audit_rows(session_factory)
    assert len(rows) == 1
    assert rows[0].action == "create"
    assert rows[0].object_id == target["id"]
    assert rows[0].meta == {"email": "t@example.com", "role": "admin", "is_active": True}


# --- PUT /users/{id}/password ----------------------------------------------


async def test_admin_resets_password(
    db_client: AsyncClient, admin_token: str, session_factory: async_sessionmaker
) -> None:
    target = await _create(db_client, admin_token, "t@example.com")
    resp = await db_client.put(
        f"{USERS}/{target['id']}/password",
        headers=_auth(admin_token),
        json={"password": "brandnew123"},
    )
    assert resp.status_code == 204, resp.text
    assert await _login(db_client, "t@example.com", "brandnew123")
    old = await db_client.post(
        "/api/v1/auth/login", json={"email": "t@example.com", "password": "password123"}
    )
    assert old.status_code == 401
    rows = [r for r in await _audit_rows(session_factory) if r.action == "update"]
    assert rows[-1].meta == {"password_reset": True}
    assert "brandnew123" not in str(rows[-1].meta)


async def test_reset_password_denied_to_member_and_404_for_unknown(
    db_client: AsyncClient, admin_token: str, member_token: str
) -> None:
    target = await _create(db_client, admin_token, "t@example.com")
    denied = await db_client.put(
        f"{USERS}/{target['id']}/password",
        headers=_auth(member_token),
        json={"password": "brandnew123"},
    )
    assert denied.status_code == 403
    missing = await db_client.put(
        f"{USERS}/999999/password", headers=_auth(admin_token), json={"password": "brandnew123"}
    )
    assert missing.status_code == 404


# --- DELETE /users/{id} -----------------------------------------------------


async def test_admin_deletes_user_and_their_token_stops_working(
    db_client: AsyncClient, admin_token: str, session_factory: async_sessionmaker
) -> None:
    target = await _create(db_client, admin_token, "t@example.com")
    target_token = await _login(db_client, "t@example.com", "password123")

    resp = await db_client.delete(f"{USERS}/{target['id']}", headers=_auth(admin_token))
    assert resp.status_code == 204

    listed = (await db_client.get(USERS, headers=_auth(admin_token))).json()
    assert all(u["email"] != "t@example.com" for u in listed)
    assert (await db_client.get(ME, headers=_auth(target_token))).status_code == 401

    rows = await _audit_rows(session_factory)
    assert rows[-1].action == "delete"
    assert rows[-1].object_id == target["id"]
    assert rows[-1].meta == {"email": "t@example.com", "role": "member"}


async def test_delete_denied_to_member_and_404_for_unknown(
    db_client: AsyncClient, admin_token: str, member_token: str
) -> None:
    target = await _create(db_client, admin_token, "t@example.com")
    denied = await db_client.delete(f"{USERS}/{target['id']}", headers=_auth(member_token))
    assert denied.status_code == 403
    missing = await db_client.delete(f"{USERS}/999999", headers=_auth(admin_token))
    assert missing.status_code == 404


# --- self-service -------------------------------------------------------------


async def test_member_updates_own_display_name_only(
    db_client: AsyncClient, member_token: str, session_factory: async_sessionmaker
) -> None:
    resp = await db_client.patch(ME, headers=_auth(member_token), json={"full_name": "Renamed"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["full_name"] == "Renamed"
    assert resp.json()["role"] == "member"

    # Role/active are not part of the profile schema — rejected outright.
    resp = await db_client.patch(
        ME, headers=_auth(member_token), json={"full_name": "x", "role": "admin"}
    )
    assert resp.status_code == 422

    rows = await _audit_rows(session_factory)
    assert rows[-1].actor == "member@example.com"
    assert rows[-1].meta == {"changes": {"full_name": ["Member User", "Renamed"]}}


async def test_profile_update_requires_authentication(db_client: AsyncClient) -> None:
    assert (await db_client.patch(ME, json={"full_name": "x"})).status_code == 401


async def test_member_changes_own_password(
    db_client: AsyncClient, member_token: str, session_factory: async_sessionmaker
) -> None:
    # No current-password check: a signed-in session is the only proof needed.
    ok = await db_client.put(
        f"{ME}/password", headers=_auth(member_token), json={"new_password": "brandnew123"}
    )
    assert ok.status_code == 204, ok.text
    assert await _login(db_client, "member@example.com", "brandnew123")
    old = await db_client.post(
        "/api/v1/auth/login", json={"email": "member@example.com", "password": "memberpass123"}
    )
    assert old.status_code == 401

    too_short = await db_client.put(
        f"{ME}/password", headers=_auth(member_token), json={"new_password": "short"}
    )
    assert too_short.status_code == 422

    rows = await _audit_rows(session_factory)
    assert rows[-1].actor == "member@example.com"
    assert rows[-1].meta == {"password_changed": True}
    assert "brandnew123" not in str(rows[-1].meta)


async def test_password_change_requires_authentication(db_client: AsyncClient) -> None:
    resp = await db_client.put(f"{ME}/password", json={"new_password": "brandnew123"})
    assert resp.status_code == 401
