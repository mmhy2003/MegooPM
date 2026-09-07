"""The task routes were mounted with no dependency at all.

Anyone who could reach the API could enqueue work and read task results
without signing in. Status is a member read because every page polls it after
a config write; enqueuing is an admin action because it starts work.
"""

from __future__ import annotations

from httpx import AsyncClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_a_stranger_cannot_read_a_task(db_client: AsyncClient) -> None:
    resp = await db_client.get("/api/v1/tasks/some-task-id")
    assert resp.status_code == 401


async def test_a_stranger_cannot_enqueue_work(db_client: AsyncClient) -> None:
    resp = await db_client.post("/api/v1/tasks/sample", json={"x": 1, "y": 2})
    assert resp.status_code == 401


async def test_a_member_cannot_read_a_task(db_client: AsyncClient, member_token: str) -> None:
    # Pages poll this after a config write, and a member can no longer start
    # one: the poll goes with the write it exists to follow.
    resp = await db_client.get("/api/v1/tasks/some-task-id", headers=_auth(member_token))
    assert resp.status_code == 403


async def test_an_admin_may_read_a_task(db_client: AsyncClient, admin_token: str) -> None:
    # The poll that follows every config write; an unknown id is not found,
    # never refused.
    resp = await db_client.get("/api/v1/tasks/some-task-id", headers=_auth(admin_token))
    assert resp.status_code not in (401, 403)


async def test_a_member_cannot_enqueue_work(db_client: AsyncClient, member_token: str) -> None:
    resp = await db_client.post(
        "/api/v1/tasks/sample", headers=_auth(member_token), json={"x": 1, "y": 2}
    )
    assert resp.status_code == 403
