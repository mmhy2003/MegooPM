"""Initial-setup admin seeding (``ensure_first_admin``).

On a fresh install the users table is empty and creating users is admin-only,
so the backend seeds one admin from ``FIRST_ADMIN_EMAIL`` / ``FIRST_ADMIN_PASSWORD``
at startup. The seed must be a true *initial setup* step: it only fires when no
user exists at all, so deleting or renaming the default account later never
resurrects it.
"""

from __future__ import annotations

import logging

import pytest
from app import main as app_main
from app.core.config import settings
from app.models.user import UserRole
from app.services import user as user_service
from sqlalchemy.ext.asyncio import async_sessionmaker

DEFAULT_EMAIL = "admin@example.com"
DEFAULT_PASSWORD = "changeme"


# --- service: ensure_first_admin -------------------------------------------


async def test_seeds_active_admin_when_no_users_exist(session_factory: async_sessionmaker):
    async with session_factory() as session:
        created = await user_service.ensure_first_admin(
            session, email="Admin@Example.com", password=DEFAULT_PASSWORD
        )

    assert created is not None
    assert created.email == DEFAULT_EMAIL  # normalised like every other create path
    assert created.role == UserRole.admin
    assert created.is_active is True

    async with session_factory() as session:
        # The seeded account is a real, usable login.
        assert await user_service.authenticate(
            session, email=DEFAULT_EMAIL, password=DEFAULT_PASSWORD
        )


async def test_is_a_noop_when_any_user_already_exists(session_factory: async_sessionmaker):
    async with session_factory() as session:
        await user_service.create_user(
            session, email="someone@example.com", password="memberpass123"
        )

    async with session_factory() as session:
        created = await user_service.ensure_first_admin(
            session, email=DEFAULT_EMAIL, password=DEFAULT_PASSWORD
        )
        assert created is None
        # Not even the default admin is added: initial setup already happened.
        assert await user_service.get_by_email(session, DEFAULT_EMAIL) is None
        assert len(await user_service.list_users(session)) == 1


async def test_is_a_noop_when_credentials_are_not_configured(
    session_factory: async_sessionmaker,
):
    async with session_factory() as session:
        assert await user_service.ensure_first_admin(session, email=None, password=None) is None
        assert (
            await user_service.ensure_first_admin(session, email=DEFAULT_EMAIL, password=None)
            is None
        )
        assert (
            await user_service.ensure_first_admin(session, email="", password=DEFAULT_PASSWORD)
            is None
        )
        assert await user_service.list_users(session) == []


async def test_is_idempotent_across_restarts(session_factory: async_sessionmaker):
    async with session_factory() as session:
        first = await user_service.ensure_first_admin(
            session, email=DEFAULT_EMAIL, password=DEFAULT_PASSWORD
        )
    async with session_factory() as session:
        second = await user_service.ensure_first_admin(
            session, email=DEFAULT_EMAIL, password=DEFAULT_PASSWORD
        )
        assert first is not None
        assert second is None
        assert len(await user_service.list_users(session)) == 1


async def test_warns_when_seeding_the_well_known_default_password(
    session_factory: async_sessionmaker, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.WARNING, logger="app.services.user")
    async with session_factory() as session:
        await user_service.ensure_first_admin(
            session, email=DEFAULT_EMAIL, password=DEFAULT_PASSWORD
        )

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "seeding the default password must be loud in the startup log"
    assert "change" in warnings[0].getMessage().lower()
    assert DEFAULT_PASSWORD not in warnings[0].getMessage()  # never log the secret


async def test_does_not_warn_for_a_custom_password(
    session_factory: async_sessionmaker, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.WARNING, logger="app.services.user")
    async with session_factory() as session:
        await user_service.ensure_first_admin(
            session, email=DEFAULT_EMAIL, password="a-real-secret-42"
        )
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


# --- startup wiring: app.main bootstraps from settings ---------------------


async def test_startup_seeds_from_settings(
    session_factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(app_main, "SessionLocal", session_factory)
    monkeypatch.setattr(settings, "first_admin_email", DEFAULT_EMAIL)
    monkeypatch.setattr(settings, "first_admin_password", DEFAULT_PASSWORD)

    await app_main._bootstrap_first_admin()

    async with session_factory() as session:
        user = await user_service.get_by_email(session, DEFAULT_EMAIL)
        assert user is not None and user.role == UserRole.admin


async def test_startup_skips_when_settings_are_unset(
    session_factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(app_main, "SessionLocal", session_factory)
    monkeypatch.setattr(settings, "first_admin_email", None)
    monkeypatch.setattr(settings, "first_admin_password", None)

    await app_main._bootstrap_first_admin()

    async with session_factory() as session:
        assert await user_service.list_users(session) == []


async def test_startup_never_raises_when_the_database_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    class _BrokenSessionLocal:
        def __call__(self):
            raise ConnectionError("db down")

    monkeypatch.setattr(app_main, "SessionLocal", _BrokenSessionLocal())
    monkeypatch.setattr(settings, "first_admin_email", DEFAULT_EMAIL)
    monkeypatch.setattr(settings, "first_admin_password", DEFAULT_PASSWORD)
    caplog.set_level(logging.WARNING, logger="app.main")

    await app_main._bootstrap_first_admin()  # must not propagate

    assert any("db down" in r.getMessage() for r in caplog.records)
