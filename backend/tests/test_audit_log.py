"""Tests for the audit-log write path (service) and read endpoint.

The write-path integration into privileged mutation handlers lands incrementally
with MEG-17/19/21/24; these tests cover the reusable pieces that exist now:
``record_audit`` / ``list_audit_logs`` and ``GET /api/v1/audit-log`` (ordering,
filtering, pagination, and admin-only authz).
"""

from __future__ import annotations

from app.models.enums import AuditAction
from app.services import audit as audit_service
from httpx import AsyncClient


async def _seed(session_factory) -> None:
    """Insert a small, deterministic set of audit rows (oldest → newest)."""
    async with session_factory() as session:
        await audit_service.record_audit(
            session,
            actor="admin@example.com",
            action=AuditAction.create,
            object_type="proxy_host",
            object_id=1,
            meta={"domain_names": ["a.example.com"]},
        )
        await audit_service.record_audit(
            session,
            actor="admin@example.com",
            action=AuditAction.update,
            object_type="proxy_host",
            object_id=1,
        )
        await audit_service.record_audit(
            session,
            actor=None,  # system action
            action=AuditAction.disable,
            object_type="stream",
            object_id=7,
        )
        await session.commit()


async def test_record_audit_persists_row_without_committing(session_factory) -> None:
    """``record_audit`` flushes (id available) but leaves the commit to caller."""
    async with session_factory() as session:
        entry = await audit_service.record_audit(
            session,
            actor="admin@example.com",
            action=AuditAction.create,
            object_type="upstream",
            object_id=42,
            meta={"name": "pool-a"},
        )
        assert entry.id is not None
        assert entry.meta == {"name": "pool-a"}
        # Not yet committed: a fresh session must not see it.
    async with session_factory() as other:
        rows, total = await audit_service.list_audit_logs(other)
        assert total == 0
        assert rows == []


async def test_record_audit_defaults_meta_to_empty_dict(session_factory) -> None:
    async with session_factory() as session:
        entry = await audit_service.record_audit(
            session,
            actor=None,
            action=AuditAction.delete,
            object_type="certificate",
        )
        await session.commit()
        assert entry.meta == {}
        assert entry.object_id is None
        assert entry.actor is None


async def test_list_audit_logs_newest_first_and_filters(session_factory) -> None:
    await _seed(session_factory)
    async with session_factory() as session:
        rows, total = await audit_service.list_audit_logs(session)
        assert total == 3
        # Newest first: the last inserted (stream/disable) leads.
        assert [r.object_type for r in rows] == ["stream", "proxy_host", "proxy_host"]

        rows, total = await audit_service.list_audit_logs(session, object_type="proxy_host")
        assert total == 2
        assert all(r.object_type == "proxy_host" for r in rows)

        rows, total = await audit_service.list_audit_logs(session, action=AuditAction.disable)
        assert total == 1
        assert rows[0].object_type == "stream"

        rows, total = await audit_service.list_audit_logs(session, actor="admin@example.com")
        assert total == 2


async def test_list_audit_logs_pagination(session_factory) -> None:
    await _seed(session_factory)
    async with session_factory() as session:
        page1, total = await audit_service.list_audit_logs(session, limit=2, offset=0)
        page2, _ = await audit_service.list_audit_logs(session, limit=2, offset=2)
        assert total == 3
        assert len(page1) == 2
        assert len(page2) == 1
        # No overlap across pages.
        ids = {r.id for r in page1} | {r.id for r in page2}
        assert len(ids) == 3


# --- Endpoint tests --------------------------------------------------------


async def test_read_endpoint_requires_auth(db_client: AsyncClient) -> None:
    resp = await db_client.get("/api/v1/audit-log")
    assert resp.status_code == 401


async def test_read_endpoint_forbids_non_admin(
    db_client: AsyncClient, member_token: str
) -> None:
    resp = await db_client.get(
        "/api/v1/audit-log", headers={"Authorization": f"Bearer {member_token}"}
    )
    assert resp.status_code == 403


async def test_read_endpoint_returns_page(
    db_client: AsyncClient, admin_token: str, session_factory
) -> None:
    await _seed(session_factory)
    resp = await db_client.get(
        "/api/v1/audit-log", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert len(body["items"]) == 3
    # Newest first.
    assert body["items"][0]["object_type"] == "stream"
    assert body["items"][0]["actor"] is None
    assert body["items"][0]["action"] == "disable"


async def test_read_endpoint_filters(
    db_client: AsyncClient, admin_token: str, session_factory
) -> None:
    await _seed(session_factory)
    headers = {"Authorization": f"Bearer {admin_token}"}
    resp = await db_client.get(
        "/api/v1/audit-log",
        params={"object_type": "proxy_host", "object_id": 1, "action": "create"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "create"
    assert body["items"][0]["object_id"] == 1


async def test_read_endpoint_rejects_bad_action(
    db_client: AsyncClient, admin_token: str
) -> None:
    resp = await db_client.get(
        "/api/v1/audit-log",
        params={"action": "not-a-real-action"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422
