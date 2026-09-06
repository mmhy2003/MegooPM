"""The four routes, driven from a browser session."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.audit_log import AuditLog
from httpx import AsyncClient
from sqlalchemy import select

KEYS = "/api/v1/users/me/api-keys"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_a_new_account_has_no_keys(db_client: AsyncClient, admin_token: str) -> None:
    resp = await db_client.get(KEYS, headers=_auth(admin_token))

    assert resp.status_code == 200
    assert resp.json() == []


async def test_creating_one_returns_the_token_exactly_once(
    db_client: AsyncClient, admin_token: str
) -> None:
    created = await db_client.post(
        KEYS, headers=_auth(admin_token), json={"name": "CI deploy", "expires_at": None}
    )

    assert created.status_code == 201, created.text
    token = created.json()["token"]
    assert token.startswith("mgm_")

    listed = (await db_client.get(KEYS, headers=_auth(admin_token))).json()
    assert len(listed) == 1
    # The listing must never carry it, and must carry enough to identify it.
    assert "token" not in listed[0]
    assert token.startswith(listed[0]["token_prefix"])
    assert listed[0]["name"] == "CI deploy"
    assert listed[0]["enabled"] is True
    assert listed[0]["expires_at"] is None


async def test_a_key_can_be_disabled_and_enabled_again(
    db_client: AsyncClient, admin_token: str
) -> None:
    key_id = (await db_client.post(KEYS, headers=_auth(admin_token), json={"name": "CI"})).json()[
        "id"
    ]

    off = await db_client.patch(
        f"{KEYS}/{key_id}", headers=_auth(admin_token), json={"enabled": False}
    )
    on = await db_client.patch(
        f"{KEYS}/{key_id}", headers=_auth(admin_token), json={"enabled": True}
    )

    assert off.json()["enabled"] is False
    assert on.json()["enabled"] is True


async def test_deleting_one_removes_it(db_client: AsyncClient, admin_token: str) -> None:
    key_id = (await db_client.post(KEYS, headers=_auth(admin_token), json={"name": "CI"})).json()[
        "id"
    ]

    resp = await db_client.delete(f"{KEYS}/{key_id}", headers=_auth(admin_token))

    assert resp.status_code == 204
    assert (await db_client.get(KEYS, headers=_auth(admin_token))).json() == []


async def test_a_revoked_key_stops_working(db_client: AsyncClient, admin_token: str) -> None:
    # The end the whole feature exists to make possible.
    created = (
        await db_client.post(KEYS, headers=_auth(admin_token), json={"name": "CI"})
    ).json()
    assert (
        await db_client.get("/api/v1/custom-pages", headers=_auth(created["token"]))
    ).status_code == 200

    await db_client.delete(f"{KEYS}/{created['id']}", headers=_auth(admin_token))

    resp = await db_client.get("/api/v1/custom-pages", headers=_auth(created["token"]))
    assert resp.status_code == 401


async def test_an_expiry_is_kept_and_returned(db_client: AsyncClient, admin_token: str) -> None:
    when = datetime.now(UTC) + timedelta(days=30)

    created = await db_client.post(
        KEYS, headers=_auth(admin_token), json={"name": "CI", "expires_at": when.isoformat()}
    )

    assert created.status_code == 201, created.text
    assert created.json()["expires_at"] is not None


async def test_an_expiry_in_the_past_is_refused(db_client: AsyncClient, admin_token: str) -> None:
    # A key that is born expired is a mistake, not a configuration.
    resp = await db_client.post(
        KEYS,
        headers=_auth(admin_token),
        json={"name": "CI", "expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat()},
    )

    assert resp.status_code == 422


async def test_a_name_is_required(db_client: AsyncClient, admin_token: str) -> None:
    # Unnamed keys are how a list of five credentials becomes unrevocable: no
    # one dares delete any of them.
    blank = await db_client.post(KEYS, headers=_auth(admin_token), json={"name": "   "})
    missing = await db_client.post(KEYS, headers=_auth(admin_token), json={})

    assert blank.status_code == 422
    assert missing.status_code == 422


async def test_a_name_is_trimmed(db_client: AsyncClient, admin_token: str) -> None:
    created = await db_client.post(KEYS, headers=_auth(admin_token), json={"name": "  CI  "})

    assert created.json()["name"] == "CI"


async def test_someone_elses_key_is_not_found(
    db_client: AsyncClient, admin_token: str, member_token: str
) -> None:
    # 404, not 403: the endpoint must not confirm that the key exists.
    key_id = (await db_client.post(KEYS, headers=_auth(admin_token), json={"name": "CI"})).json()[
        "id"
    ]

    patched = await db_client.patch(
        f"{KEYS}/{key_id}", headers=_auth(member_token), json={"enabled": False}
    )
    deleted = await db_client.delete(f"{KEYS}/{key_id}", headers=_auth(member_token))

    assert patched.status_code == 404
    assert deleted.status_code == 404


async def test_a_list_shows_only_your_own(
    db_client: AsyncClient, admin_token: str, member_token: str
) -> None:
    await db_client.post(KEYS, headers=_auth(admin_token), json={"name": "theirs"})

    assert (await db_client.get(KEYS, headers=_auth(member_token))).json() == []


async def test_a_member_can_hold_keys_too(db_client: AsyncClient, member_token: str) -> None:
    # Their key is read-only because their role is, which is the whole design.
    resp = await db_client.post(KEYS, headers=_auth(member_token), json={"name": "reporting"})

    assert resp.status_code == 201


async def test_the_twenty_first_key_is_refused(db_client: AsyncClient, admin_token: str) -> None:
    for i in range(20):
        await db_client.post(KEYS, headers=_auth(admin_token), json={"name": f"k{i}"})

    resp = await db_client.post(KEYS, headers=_auth(admin_token), json={"name": "one too many"})

    assert resp.status_code == 422
    assert "20" in resp.text


async def test_creating_and_revoking_are_audited(
    db_client: AsyncClient, admin_token: str, session_factory
) -> None:
    key_id = (await db_client.post(KEYS, headers=_auth(admin_token), json={"name": "CI"})).json()[
        "id"
    ]
    await db_client.patch(f"{KEYS}/{key_id}", headers=_auth(admin_token), json={"enabled": False})
    await db_client.delete(f"{KEYS}/{key_id}", headers=_auth(admin_token))

    async with session_factory() as db:
        rows = list(await db.scalars(select(AuditLog).where(AuditLog.object_type == "api_key")))

    assert len(rows) == 3
    # The name, so the log reads as "CI was created, disabled, deleted" — and
    # never the token or its digest.
    assert all("CI" in str(row.meta) for row in rows)
    assert all("mgm_" not in str(row.meta) for row in rows)
