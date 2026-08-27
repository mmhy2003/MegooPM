"""Service-level behaviour for user management: lock-out guards, passwords, delete."""

from __future__ import annotations

import pytest
from app.models.user import User, UserRole
from app.services import user as user_service
from sqlalchemy.ext.asyncio import async_sessionmaker


async def _make(
    session_factory: async_sessionmaker, email: str, role: UserRole, *, active: bool = True
) -> User:
    async with session_factory() as session:
        return await user_service.create_user(
            session, email=email, password="password123", role=role, is_active=active
        )


# --- count_active_admins ---------------------------------------------------


async def test_count_active_admins_ignores_members_and_inactive_admins(session_factory):
    await _make(session_factory, "a1@example.com", UserRole.admin)
    await _make(session_factory, "a2@example.com", UserRole.admin, active=False)
    await _make(session_factory, "m@example.com", UserRole.member)
    async with session_factory() as session:
        assert await user_service.count_active_admins(session) == 1


# --- assert_no_lockout -----------------------------------------------------


async def test_actor_cannot_change_own_role(session_factory):
    me = await _make(session_factory, "me@example.com", UserRole.admin)
    await _make(session_factory, "other@example.com", UserRole.admin)
    async with session_factory() as session:
        with pytest.raises(user_service.UserProtectionError, match="own role"):
            await user_service.assert_no_lockout(session, me, actor=me, new_role=UserRole.member)


async def test_actor_setting_own_role_to_same_value_is_allowed(session_factory):
    me = await _make(session_factory, "me@example.com", UserRole.admin)
    async with session_factory() as session:
        await user_service.assert_no_lockout(session, me, actor=me, new_role=UserRole.admin)


async def test_actor_cannot_deactivate_or_delete_self(session_factory):
    me = await _make(session_factory, "me@example.com", UserRole.admin)
    await _make(session_factory, "other@example.com", UserRole.admin)
    async with session_factory() as session:
        with pytest.raises(user_service.UserProtectionError, match="deactivate your own"):
            await user_service.assert_no_lockout(session, me, actor=me, new_active=False)
        with pytest.raises(user_service.UserProtectionError, match="delete your own"):
            await user_service.assert_no_lockout(session, me, actor=me, deleting=True)


async def test_last_active_admin_is_protected_from_another_actor(session_factory):
    only_admin = await _make(session_factory, "only@example.com", UserRole.admin)
    # A second, *inactive* admin does not count.
    await _make(session_factory, "sleeping@example.com", UserRole.admin, active=False)
    actor = await _make(session_factory, "m@example.com", UserRole.member)
    async with session_factory() as session:
        for kwargs in ({"new_role": UserRole.member}, {"new_active": False}, {"deleting": True}):
            with pytest.raises(user_service.UserProtectionError, match="last active admin"):
                await user_service.assert_no_lockout(session, only_admin, actor=actor, **kwargs)


async def test_admin_can_be_demoted_when_another_active_admin_exists(session_factory):
    a = await _make(session_factory, "a@example.com", UserRole.admin)
    b = await _make(session_factory, "b@example.com", UserRole.admin)
    async with session_factory() as session:
        await user_service.assert_no_lockout(session, b, actor=a, new_role=UserRole.member)
        await user_service.assert_no_lockout(session, b, actor=a, new_active=False)
        await user_service.assert_no_lockout(session, b, actor=a, deleting=True)


# --- update_user -----------------------------------------------------------


async def test_update_user_applies_fields_and_reports_changes(session_factory):
    a = await _make(session_factory, "a@example.com", UserRole.admin)
    m = await _make(session_factory, "m@example.com", UserRole.member)
    async with session_factory() as session:
        m = await user_service.get_by_id(session, m.id)
        updated, changes = await user_service.update_user(
            session, m, actor=a, full_name="Mem Ber", role=UserRole.admin
        )
        assert updated.full_name == "Mem Ber"
        assert updated.role == UserRole.admin
        assert changes == {"full_name": ["", "Mem Ber"], "role": ["member", "admin"]}


async def test_update_user_reports_no_changes_for_identical_values(session_factory):
    a = await _make(session_factory, "a@example.com", UserRole.admin)
    m = await _make(session_factory, "m@example.com", UserRole.member)
    async with session_factory() as session:
        m = await user_service.get_by_id(session, m.id)
        _, changes = await user_service.update_user(session, m, actor=a, role=UserRole.member)
        assert changes == {}


async def test_update_user_enforces_guards(session_factory):
    a = await _make(session_factory, "a@example.com", UserRole.admin)
    async with session_factory() as session:
        a = await user_service.get_by_id(session, a.id)
        with pytest.raises(user_service.UserProtectionError):
            await user_service.update_user(session, a, actor=a, is_active=False)


# --- passwords -------------------------------------------------------------


async def test_set_password_replaces_credentials(session_factory):
    m = await _make(session_factory, "m@example.com", UserRole.member)
    async with session_factory() as session:
        m = await user_service.get_by_id(session, m.id)
        await user_service.set_password(session, m, "brandnew123")
    async with session_factory() as session:
        assert await user_service.authenticate(
            session, email="m@example.com", password="brandnew123"
        )
        assert not await user_service.authenticate(
            session, email="m@example.com", password="password123"
        )


async def test_change_own_password_replaces_credentials_without_the_old_one(session_factory):
    m = await _make(session_factory, "m@example.com", UserRole.member)
    async with session_factory() as session:
        m = await user_service.get_by_id(session, m.id)
        await user_service.change_own_password(session, m, new_password="brandnew123")
    async with session_factory() as session:
        assert await user_service.authenticate(
            session, email="m@example.com", password="brandnew123"
        )
        assert not await user_service.authenticate(
            session, email="m@example.com", password="password123"
        )


# --- delete ----------------------------------------------------------------


async def test_delete_user_removes_row(session_factory):
    a = await _make(session_factory, "a@example.com", UserRole.admin)
    m = await _make(session_factory, "m@example.com", UserRole.member)
    async with session_factory() as session:
        m = await user_service.get_by_id(session, m.id)
        await user_service.delete_user(session, m, actor=a)
        assert await user_service.get_by_email(session, "m@example.com") is None


async def test_delete_user_enforces_guards(session_factory):
    a = await _make(session_factory, "a@example.com", UserRole.admin)
    async with session_factory() as session:
        a = await user_service.get_by_id(session, a.id)
        with pytest.raises(user_service.UserProtectionError):
            await user_service.delete_user(session, a, actor=a)
