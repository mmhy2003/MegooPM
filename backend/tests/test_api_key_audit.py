"""An audit row must say which key made the change."""

from __future__ import annotations

from app.models.audit_log import AuditLog
from app.models.user import User
from app.services import api_keys
from httpx import AsyncClient
from sqlalchemy import select

PAGES = "/api/v1/custom-pages"


async def _latest_actor(session_factory) -> str | None:
    async with session_factory() as db:
        row = (await db.scalars(select(AuditLog).order_by(AuditLog.id.desc()))).first()
    assert row is not None, "the mutation wrote no audit row at all"
    return row.actor


async def test_a_change_made_with_a_key_names_the_key(
    db_client: AsyncClient, admin_user: User, session_factory
) -> None:
    async with session_factory() as db:
        owner = await db.get(User, admin_user.id)
        _, token = await api_keys.create(db, owner, name="CI deploy", expires_at=None)

    resp = await db_client.post(
        PAGES,
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "From CI", "html": "<h1>hi</h1>"},
    )

    assert resp.status_code == 201, resp.text
    actor = await _latest_actor(session_factory)
    assert admin_user.email in actor
    assert "CI deploy" in actor


async def test_a_change_made_in_a_browser_names_no_key(
    db_client: AsyncClient, admin_token: str, admin_user: User, session_factory
) -> None:
    # The other half of the guarantee: the marker must not leak between
    # requests that share a worker.
    resp = await db_client.post(
        PAGES,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "By hand", "html": "<h1>hi</h1>"},
    )

    assert resp.status_code == 201, resp.text
    assert await _latest_actor(session_factory) == admin_user.email


async def test_a_session_request_after_a_key_request_is_clean(
    db_client: AsyncClient, admin_token: str, admin_user: User, session_factory
) -> None:
    """The leak this ordering would expose is the reason the var is always set."""
    async with session_factory() as db:
        owner = await db.get(User, admin_user.id)
        _, token = await api_keys.create(db, owner, name="CI deploy", expires_at=None)

    await db_client.post(
        PAGES,
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "From CI", "html": "<h1>hi</h1>"},
    )
    await db_client.post(
        PAGES,
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "By hand", "html": "<h1>hi</h1>"},
    )

    assert await _latest_actor(session_factory) == admin_user.email
