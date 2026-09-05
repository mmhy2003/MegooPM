"""Whitelist CRUD, preview and apply-status routes (admin-only).

Needs a reachable Postgres (skipped otherwise): ``ips``/``cidrs`` are ``ARRAY``
columns. Every request runs inside one outer transaction rolled back on
teardown.

``CROWDSEC_CONTROL_NODE_ID`` is unset in tests, so ``_enqueue_apply`` returns
False without touching a broker — which is also the behaviour one of these
tests pins down.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.api.routes import crowdsec as crowdsec_routes
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.user import UserRole
from app.services import user as user_service
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.asyncio

BASE = "/api/v1/crowdsec/whitelists"


@pytest.fixture(autouse=True)
def _no_broker(monkeypatch) -> None:
    """Stub the apply enqueue: these tests have no broker.

    Every mutation now queues an apply (on a single node that goes to the
    default queue), so without this the CRUD tests fail trying to reach Redis.
    Tests that care about routing replace this with their own recorder.
    """
    monkeypatch.setattr(crowdsec_routes.celery_app, "send_task", lambda *a, **kw: None)


@pytest.fixture
async def pg_conn() -> AsyncIterator:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        conn = await engine.connect()
    except Exception:  # pragma: no cover - environment without a database
        await engine.dispose()
        pytest.skip("No database reachable at DATABASE_URL")

    trans = await conn.begin()
    await conn.run_sync(Base.metadata.create_all)
    try:
        yield conn
    finally:
        if trans.is_active:
            await trans.rollback()
        await conn.close()
        await engine.dispose()


@pytest.fixture
async def client(pg_conn) -> AsyncIterator[AsyncClient]:
    factory = async_sessionmaker(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )

    async def _override_get_session() -> AsyncIterator:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.fixture
async def auth(pg_conn, client: AsyncClient) -> dict[str, str]:
    factory = async_sessionmaker(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    async with factory() as session:
        await user_service.create_user(
            session,
            email="wl-admin@example.com",
            password="adminpass123",
            role=UserRole.admin,
        )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "wl-admin@example.com", "password": "adminpass123"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def member_auth(pg_conn, client: AsyncClient) -> dict[str, str]:
    factory = async_sessionmaker(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    async with factory() as session:
        await user_service.create_user(
            session,
            email="wl-member@example.com",
            password="memberpass123",
            role=UserRole.member,
        )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "wl-member@example.com", "password": "memberpass123"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _body(**over) -> dict:
    body = {
        "name": "Internal Backends",
        "kind": "ip_cidr",
        "reason": "internal backends trip appsec generic rules",
        "description": "",
        "ips": ["10.10.0.14"],
        "cidrs": [],
        "enabled": True,
    }
    body.update(over)
    return body


# --- validation ------------------------------------------------------------


async def test_rejects_a_bad_ip_before_writing_anything(client, auth) -> None:
    resp = await client.post(BASE, headers=auth, json=_body(ips=["10.10.0.999"]))
    assert resp.status_code == 422
    assert "10.10.0.999" in resp.text


async def test_rejects_a_bad_cidr(client, auth) -> None:
    resp = await client.post(BASE, headers=auth, json=_body(ips=[], cidrs=["10.10.0.0/99"]))
    assert resp.status_code == 422
    assert "10.10.0.0/99" in resp.text


async def test_rejects_a_whitelist_matching_nothing(client, auth) -> None:
    resp = await client.post(BASE, headers=auth, json=_body(ips=[], cidrs=[]))
    assert resp.status_code == 422


async def test_rejects_names_that_collide_after_slugification(client, auth) -> None:
    # "Internal Backends" and "internal-backends" both render
    # `megoopm/wl-internal-backends`, and CrowdSec refuses to start when two
    # loaded parsers share a name.
    first = await client.post(BASE, headers=auth, json=_body())
    assert first.status_code == 201, first.text
    second = await client.post(
        BASE, headers=auth, json=_body(name="internal-backends", ips=["10.9.9.2"])
    )
    assert second.status_code == 409
    assert "megoopm/wl-internal-backends" in second.text


# --- CRUD ------------------------------------------------------------------


async def test_create_then_list_and_toggle(client, auth) -> None:
    created = await client.post(BASE, headers=auth, json=_body())
    assert created.status_code == 201, created.text
    row_id = created.json()["id"]

    listed = await client.get(BASE, headers=auth)
    assert listed.status_code == 200
    assert [r["id"] for r in listed.json()] == [row_id]

    patched = await client.patch(f"{BASE}/{row_id}", headers=auth, json=_body(enabled=False))
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False


async def test_delete_removes_the_row(client, auth) -> None:
    created = await client.post(BASE, headers=auth, json=_body())
    row_id = created.json()["id"]
    assert (await client.delete(f"{BASE}/{row_id}", headers=auth)).status_code == 204
    assert (await client.get(BASE, headers=auth)).json() == []


async def test_patching_a_missing_whitelist_is_404(client, auth) -> None:
    resp = await client.patch(f"{BASE}/999999", headers=auth, json=_body())
    assert resp.status_code == 404


# --- preview ---------------------------------------------------------------


async def test_preview_returns_the_yaml_that_would_be_written(client, auth) -> None:
    resp = await client.post(f"{BASE}/preview", headers=auth, json=_body())
    assert resp.status_code == 200
    body = resp.json()["yaml"]
    assert "megoopm/wl-internal-backends" in body
    assert "10.10.0.14" in body


# --- status ----------------------------------------------------------------


async def test_status_reports_when_reload_is_not_configured(client, auth, monkeypatch) -> None:
    # Under HA with no control node named, there is no queue to send the apply
    # to. Saving must not silently imply the whitelist is in force.
    monkeypatch.setattr(settings, "ha_enabled", True)
    monkeypatch.setattr(settings, "crowdsec_control_node_id", None)
    resp = await client.get(f"{BASE}/status", headers=auth)
    assert resp.status_code == 200
    assert resp.json()["reload_configured"] is False


async def test_apply_refuses_when_reload_is_not_configured(client, auth, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ha_enabled", True)
    monkeypatch.setattr(settings, "crowdsec_control_node_id", None)
    resp = await client.post(f"{BASE}/apply", headers=auth)
    assert resp.status_code == 503
    assert "CROWDSEC_CONTROL_NODE_ID" in resp.text


async def test_apply_enqueues_when_a_control_node_is_set(client, auth, monkeypatch) -> None:
    sent: dict[str, object] = {}
    monkeypatch.setattr(settings, "ha_enabled", True)
    monkeypatch.setattr(settings, "crowdsec_control_node_id", "node-1")
    monkeypatch.setattr(
        crowdsec_routes.celery_app,
        "send_task",
        lambda name, queue=None: sent.update(name=name, queue=queue),
    )
    resp = await client.post(f"{BASE}/apply", headers=auth)
    assert resp.status_code == 202
    assert sent["name"] == "app.tasks.crowdsec.apply_crowdsec_whitelists"
    # Routed to that node's own queue: only it runs the CrowdSec container.
    assert sent["queue"] == "megoopm.node.node-1"


# --- authorisation ---------------------------------------------------------


async def test_non_admin_cannot_list_whitelists(client, member_auth) -> None:
    assert (await client.get(BASE, headers=member_auth)).status_code == 403


async def test_anonymous_cannot_list_whitelists(client) -> None:
    assert (await client.get(BASE)).status_code == 401


# --- expression whitelists -------------------------------------------------


def _expr_body(**over) -> dict:
    body = {
        "name": "Health checks",
        "kind": "expression",
        "reason": "GET /health",
        "description": "",
        "ips": [],
        "cidrs": [],
        "filter": "evt.Meta.service == 'http'",
        "expressions": ["evt.Meta.http_path == '/health'"],
        "enabled": True,
    }
    body.update(over)
    return body


async def test_creates_an_expression_whitelist(client, auth) -> None:
    resp = await client.post(BASE, headers=auth, json=_expr_body())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "expression"
    assert body["expressions"] == ["evt.Meta.http_path == '/health'"]


async def test_an_expression_whitelist_needs_an_expression(client, auth) -> None:
    resp = await client.post(BASE, headers=auth, json=_expr_body(expressions=[]))
    assert resp.status_code == 422
    assert "at least one expression" in resp.text


async def test_a_blank_expression_is_rejected(client, auth) -> None:
    resp = await client.post(BASE, headers=auth, json=_expr_body(expressions=["   "]))
    assert resp.status_code == 422


async def test_an_expression_whitelist_refuses_ips(client, auth) -> None:
    # Silently dropping them would be worse: CrowdSec evaluates every key it
    # finds, so an `ip:` the operator thinks is inert would widen the whitelist.
    resp = await client.post(BASE, headers=auth, json=_expr_body(ips=["10.0.0.1"]))
    assert resp.status_code == 422
    assert "cannot carry IPs" in resp.text


async def test_an_ip_whitelist_refuses_expressions(client, auth) -> None:
    resp = await client.post(BASE, headers=auth, json=_body(expressions=["evt.Meta.x == 'y'"]))
    assert resp.status_code == 422
    assert "cannot carry expressions" in resp.text


async def test_an_ip_whitelist_refuses_a_filter(client, auth) -> None:
    resp = await client.post(BASE, headers=auth, json=_body(filter="evt.Meta.x == 'y'"))
    assert resp.status_code == 422


async def test_preview_renders_an_expression_whitelist(client, auth) -> None:
    resp = await client.post(f"{BASE}/preview", headers=auth, json=_expr_body())
    assert resp.status_code == 200
    body = resp.json()["yaml"]
    assert "filter: \"evt.Meta.service == 'http'\"" in body
    assert "expression:" in body
    # Readable, not HTML-escaped into unicode escapes.
    assert chr(92) + "u00" not in body


async def test_defaults_to_the_ip_kind_when_unspecified(client, auth) -> None:
    body = _body()
    body.pop("kind", None)
    resp = await client.post(BASE, headers=auth, json=body)
    assert resp.status_code == 201
    assert resp.json()["kind"] == "ip_cidr"


# --- reload routing --------------------------------------------------------
#
# Workers only consume a `megoopm.node.<id>` queue when HA is on
# (`_configure_ha`). Addressing one on a single-node deployment would leave the
# task queued forever with nothing consuming it.


async def test_single_node_reload_is_configured_without_a_node_id(
    client, auth, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "ha_enabled", False)
    monkeypatch.setattr(settings, "crowdsec_control_node_id", None)
    resp = await client.get(f"{BASE}/status", headers=auth)
    assert resp.json()["reload_configured"] is True


async def test_single_node_apply_goes_to_the_default_queue(client, auth, monkeypatch) -> None:
    sent: dict[str, object] = {}
    monkeypatch.setattr(settings, "ha_enabled", False)
    monkeypatch.setattr(settings, "crowdsec_control_node_id", None)
    monkeypatch.setattr(
        crowdsec_routes.celery_app,
        "send_task",
        lambda name, queue=None: sent.update(name=name, queue=queue),
    )
    resp = await client.post(f"{BASE}/apply", headers=auth)
    assert resp.status_code == 202
    # No queue: the single worker consumes the default one.
    assert sent["queue"] is None


async def test_ha_without_a_control_node_is_not_configured(client, auth, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ha_enabled", True)
    monkeypatch.setattr(settings, "crowdsec_control_node_id", None)
    resp = await client.get(f"{BASE}/status", headers=auth)
    assert resp.json()["reload_configured"] is False


async def test_ha_apply_is_addressed_to_the_control_node(client, auth, monkeypatch) -> None:
    sent: dict[str, object] = {}
    monkeypatch.setattr(settings, "ha_enabled", True)
    monkeypatch.setattr(settings, "crowdsec_control_node_id", "node-1")
    monkeypatch.setattr(
        crowdsec_routes.celery_app,
        "send_task",
        lambda name, queue=None: sent.update(name=name, queue=queue),
    )
    resp = await client.post(f"{BASE}/apply", headers=auth)
    assert resp.status_code == 202
    assert sent["queue"] == "megoopm.node.node-1"
