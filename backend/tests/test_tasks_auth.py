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


async def test_a_member_may_read_a_task(db_client: AsyncClient, member_token: str) -> None:
    # Pages poll this after a config write; the id is unguessable and the
    # payload is a status, so reading one is not an admin act.
    resp = await db_client.get("/api/v1/tasks/some-task-id", headers=_auth(member_token))
    assert resp.status_code != 401
    assert resp.status_code != 403


async def test_a_member_cannot_enqueue_work(db_client: AsyncClient, member_token: str) -> None:
    resp = await db_client.post(
        "/api/v1/tasks/sample", headers=_auth(member_token), json={"x": 1, "y": 2}
    )
    assert resp.status_code == 403
