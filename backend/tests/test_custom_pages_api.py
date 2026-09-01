"""API tests for custom pages: CRUD over reusable HTML response bodies.

Like the other domain-schema suites these need a reachable Postgres (skipped
otherwise) and run inside one outer transaction rolled back on teardown.

Nothing references a page yet, so — unlike every other resource here — these
writes must NOT enqueue an nginx reload; there is no rendered config to
converge. ``test_writes_do_not_touch_nginx`` pins that.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import app.api.routes._config_writes as config_writes
import pytest
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.user import UserRole
from app.schemas.tasks import TaskEnqueued
from app.services import user as user_service
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

PAGE_HTML = "<!doctype html>\n<html><body><h1>Access denied</h1></body></html>\n"


@pytest.fixture
async def pg_conn() -> AsyncIterator:
    """A single Postgres connection in one rolled-back transaction (or skip)."""
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
async def client(pg_conn, monkeypatch) -> AsyncIterator[AsyncClient]:
    factory = async_sessionmaker(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )

    async def _override_get_session() -> AsyncIterator:
        async with factory() as session:
            yield session

    monkeypatch.setattr(
        config_writes,
        "enqueue_nginx_reload",
        lambda: TaskEnqueued(task_id="test-reload-task", status="PENDING"),
    )

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.fixture
async def admin_token(pg_conn, client: AsyncClient) -> str:
    factory = async_sessionmaker(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    async with factory() as session:
        await user_service.create_user(
            session,
            email="pages-admin@example.com",
            password="adminpass123",
            role=UserRole.admin,
        )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "pages-admin@example.com", "password": "adminpass123"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture
def auth(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


async def _create(client: AsyncClient, auth, **overrides) -> dict:
    body = {"name": "Access denied", "description": "", "html": PAGE_HTML} | overrides
    resp = await client.post("/api/v1/custom-pages", headers=auth, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- CRUD ------------------------------------------------------------------


async def test_create_returns_the_page(client: AsyncClient, auth) -> None:
    page = await _create(client, auth, description="Shown to banned clients")
    assert page["name"] == "Access denied"
    assert page["description"] == "Shown to banned clients"
    assert page["html"] == PAGE_HTML
    assert page["id"] > 0


async def test_list_omits_the_html_body(client: AsyncClient, auth) -> None:
    """The index carries a size instead of every page's full source."""
    await _create(client, auth)
    resp = await client.get("/api/v1/custom-pages", headers=auth)
    assert resp.status_code == 200, resp.text
    row = resp.json()[0]
    assert row["name"] == "Access denied"
    assert row["size_bytes"] == len(PAGE_HTML.encode("utf-8"))
    assert "html" not in row


async def test_get_update_delete_lifecycle(client: AsyncClient, auth) -> None:
    page = await _create(client, auth)
    page_id = page["id"]

    fetched = await client.get(f"/api/v1/custom-pages/{page_id}", headers=auth)
    assert fetched.status_code == 200
    assert fetched.json()["html"] == PAGE_HTML

    patched = await client.patch(
        f"/api/v1/custom-pages/{page_id}",
        headers=auth,
        json={"name": "Banned", "html": "<p>nope</p>"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Banned"
    assert patched.json()["html"] == "<p>nope</p>"

    deleted = await client.delete(f"/api/v1/custom-pages/{page_id}", headers=auth)
    assert deleted.status_code == 204
    assert (await client.get(f"/api/v1/custom-pages/{page_id}", headers=auth)).status_code == 404


async def test_unknown_page_is_404(client: AsyncClient, auth) -> None:
    assert (await client.get("/api/v1/custom-pages/9999", headers=auth)).status_code == 404
    assert (
        await client.patch("/api/v1/custom-pages/9999", headers=auth, json={"name": "x"})
    ).status_code == 404
    assert (await client.delete("/api/v1/custom-pages/9999", headers=auth)).status_code == 404


# --- Validation ------------------------------------------------------------


async def test_duplicate_name_rejected(client: AsyncClient, auth) -> None:
    await _create(client, auth)
    resp = await client.post(
        "/api/v1/custom-pages",
        headers=auth,
        json={"name": "Access denied", "html": PAGE_HTML},
    )
    assert resp.status_code == 409, resp.text


async def test_rename_onto_an_existing_name_rejected(client: AsyncClient, auth) -> None:
    await _create(client, auth)
    other = await _create(client, auth, name="Maintenance")
    resp = await client.patch(
        f"/api/v1/custom-pages/{other['id']}",
        headers=auth,
        json={"name": "Access denied"},
    )
    assert resp.status_code == 409, resp.text


async def test_blank_name_rejected(client: AsyncClient, auth) -> None:
    resp = await client.post(
        "/api/v1/custom-pages", headers=auth, json={"name": "   ", "html": PAGE_HTML}
    )
    assert resp.status_code == 422, resp.text


async def test_name_is_trimmed(client: AsyncClient, auth) -> None:
    page = await _create(client, auth, name="  Spaced  ")
    assert page["name"] == "Spaced"


async def test_oversize_html_rejected(client: AsyncClient, auth) -> None:
    """Base64 images inflate a page fast; the cap keeps one row from running away."""
    resp = await client.post(
        "/api/v1/custom-pages",
        headers=auth,
        json={"name": "Huge", "html": "x" * (2 * 1024 * 1024 + 1)},
    )
    assert resp.status_code == 422, resp.text


async def test_html_at_the_cap_is_accepted(client: AsyncClient, auth) -> None:
    resp = await client.post(
        "/api/v1/custom-pages",
        headers=auth,
        json={"name": "AtCap", "html": "x" * (2 * 1024 * 1024)},
    )
    assert resp.status_code == 201, resp.text


async def test_html_defaults_to_empty(client: AsyncClient, auth) -> None:
    resp = await client.post("/api/v1/custom-pages", headers=auth, json={"name": "Blank"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["html"] == ""


# --- Side effects ----------------------------------------------------------


async def test_writes_for_an_unreferenced_page_do_not_touch_nginx(
    client: AsyncClient, auth, monkeypatch
) -> None:
    """A page nothing serves changes no rendered config, so no reload is due."""
    calls = 0

    def _counting_reload() -> TaskEnqueued:
        nonlocal calls
        calls += 1
        return TaskEnqueued(task_id="test-reload-task", status="PENDING")

    monkeypatch.setattr(config_writes, "enqueue_nginx_reload", _counting_reload)

    page = await _create(client, auth)
    created = await client.get("/api/v1/custom-pages", headers=auth)
    assert "X-Config-Reload-Task" not in created.headers

    await client.patch(
        f"/api/v1/custom-pages/{page['id']}", headers=auth, json={"html": "<p>x</p>"}
    )
    await client.delete(f"/api/v1/custom-pages/{page['id']}", headers=auth)
    assert calls == 0


async def test_writes_are_audited(client: AsyncClient, auth) -> None:
    page = await _create(client, auth)
    await client.delete(f"/api/v1/custom-pages/{page['id']}", headers=auth)

    entries = await client.get("/api/v1/audit-log", headers=auth)
    assert entries.status_code == 200, entries.text
    items = entries.json()["items"]
    actions = {(e["action"], e["object_type"]) for e in items if e["object_type"] == "custom_page"}
    assert ("create", "custom_page") in actions
    assert ("delete", "custom_page") in actions


# --- AuthZ -----------------------------------------------------------------


async def test_endpoints_require_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/custom-pages")).status_code == 401
    assert (
        await client.post("/api/v1/custom-pages", json={"name": "x", "html": ""})
    ).status_code == 401


# --- Coupling to the default site ------------------------------------------


async def _point_default_site_at(client: AsyncClient, auth, page_id: int) -> None:
    resp = await client.patch(
        "/api/v1/settings/default-site",
        headers=auth,
        json={"default_site_mode": "custom_page", "default_site_page_id": page_id},
    )
    assert resp.status_code == 200, resp.text


def _counting_reload_into(box: list[int]):
    def _reload() -> TaskEnqueued:
        box.append(1)
        return TaskEnqueued(task_id="test-reload-task", status="PENDING")

    return _reload


async def test_deleting_a_page_the_default_site_uses_is_refused(client: AsyncClient, auth) -> None:
    """Silently changing what every unmatched visitor sees is worse than a 409."""
    page = await _create(client, auth)
    await _point_default_site_at(client, auth, page["id"])

    resp = await client.delete(f"/api/v1/custom-pages/{page['id']}", headers=auth)
    assert resp.status_code == 409, resp.text
    assert "default site" in resp.text.lower()

    # Still there.
    assert (await client.get(f"/api/v1/custom-pages/{page['id']}", headers=auth)).status_code == 200


async def test_deleting_an_unreferenced_page_still_works(client: AsyncClient, auth) -> None:
    page = await _create(client, auth)
    assert (
        await client.delete(f"/api/v1/custom-pages/{page['id']}", headers=auth)
    ).status_code == 204


async def test_editing_an_unreferenced_page_enqueues_no_reload(
    client: AsyncClient, auth, monkeypatch
) -> None:
    page = await _create(client, auth)
    calls: list[int] = []
    monkeypatch.setattr(config_writes, "enqueue_nginx_reload", _counting_reload_into(calls))

    resp = await client.patch(
        f"/api/v1/custom-pages/{page['id']}", headers=auth, json={"html": "<p>x</p>"}
    )
    assert resp.status_code == 200, resp.text
    assert calls == []


async def test_editing_the_page_the_default_site_uses_enqueues_one_reload(
    client: AsyncClient, auth, monkeypatch
) -> None:
    """Otherwise the edit lands in the database and never reaches the visitor."""
    page = await _create(client, auth)
    await _point_default_site_at(client, auth, page["id"])

    calls: list[int] = []
    monkeypatch.setattr(config_writes, "enqueue_nginx_reload", _counting_reload_into(calls))

    resp = await client.patch(
        f"/api/v1/custom-pages/{page['id']}", headers=auth, json={"html": "<p>new</p>"}
    )
    assert resp.status_code == 200, resp.text
    assert len(calls) == 1
    assert resp.headers["X-Config-Reload-Task"] == "test-reload-task"


# --- AI assistance ---------------------------------------------------------

ASSIST_DOC = "<!doctype html>\n<html><body><h1>done</h1></body></html>"


async def _enable_llm(client: AsyncClient, auth) -> None:
    resp = await client.patch(
        "/api/v1/settings/llm",
        headers=auth,
        json={
            "llm_enabled": True,
            "llm_model": "gpt-4o",
            "llm_api_key": "sk-EXAMPLE-not-a-real-credential-1",
        },
    )
    assert resp.status_code == 200, resp.text


@pytest.fixture
def stub_assist(monkeypatch):
    """Replace the model round trip; these tests are about the route."""
    import app.api.routes.custom_pages as routes

    seen: list[dict] = []

    async def _assist(config, *, instruction, html, timeout=120.0):
        seen.append({"config": config, "instruction": instruction, "html": html})
        return ASSIST_DOC

    monkeypatch.setattr(routes, "assist_page", _assist)
    return seen


async def test_assist_returns_a_document(client: AsyncClient, auth, stub_assist) -> None:
    await _enable_llm(client, auth)
    resp = await client.post(
        "/api/v1/custom-pages/assist",
        headers=auth,
        json={"instruction": "make it blue", "html": "<p>x</p>"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"html": ASSIST_DOC}
    assert stub_assist[0]["instruction"] == "make it blue"
    assert stub_assist[0]["html"] == "<p>x</p>"
    assert stub_assist[0]["config"].model == "gpt-4o"


async def test_assist_works_with_no_document(client: AsyncClient, auth, stub_assist) -> None:
    """Generating from nothing is the same endpoint."""
    await _enable_llm(client, auth)
    resp = await client.post(
        "/api/v1/custom-pages/assist",
        headers=auth,
        json={"instruction": "a 503 maintenance page"},
    )
    assert resp.status_code == 200, resp.text
    assert stub_assist[0]["html"] == ""


async def test_assist_is_refused_while_llm_is_off(client: AsyncClient, auth) -> None:
    """Unlike the settings probe, this is feature code and the flag gates it."""
    resp = await client.post(
        "/api/v1/custom-pages/assist",
        headers=auth,
        json={"instruction": "make it blue", "html": "<p>x</p>"},
    )
    assert resp.status_code == 422, resp.text
    assert "settings" in resp.text.lower()


async def test_assist_rejects_an_empty_instruction(client: AsyncClient, auth) -> None:
    await _enable_llm(client, auth)
    for instruction in ("", "   "):
        resp = await client.post(
            "/api/v1/custom-pages/assist",
            headers=auth,
            json={"instruction": instruction, "html": "<p>x</p>"},
        )
        assert resp.status_code == 422, resp.text


async def test_assist_rejects_an_overlong_instruction(client: AsyncClient, auth) -> None:
    """An unbounded field is an unmetered path into a paid API."""
    await _enable_llm(client, auth)
    resp = await client.post(
        "/api/v1/custom-pages/assist",
        headers=auth,
        json={"instruction": "x" * 2001, "html": "<p>x</p>"},
    )
    assert resp.status_code == 422, resp.text


async def test_assist_rejects_an_un_elided_document(client: AsyncClient, auth) -> None:
    """The client elides before sending; this catches one that did not."""
    await _enable_llm(client, auth)
    resp = await client.post(
        "/api/v1/custom-pages/assist",
        headers=auth,
        json={"instruction": "tidy", "html": "x" * (200 * 1024 + 1)},
    )
    assert resp.status_code == 422, resp.text


async def test_a_provider_failure_is_502_not_422(client: AsyncClient, auth, monkeypatch) -> None:
    """The request was fine and the client can do nothing about it; the
    upstream failed. 422 would blur that in the logs."""
    import app.api.routes.custom_pages as routes
    from app.services.llm import LlmError

    async def _boom(config, *, instruction, html, timeout=120.0):
        raise LlmError("provider said no")

    monkeypatch.setattr(routes, "assist_page", _boom)

    await _enable_llm(client, auth)
    resp = await client.post(
        "/api/v1/custom-pages/assist",
        headers=auth,
        json={"instruction": "make it blue", "html": "<p>x</p>"},
    )
    assert resp.status_code == 502, resp.text
    assert "provider said no" in resp.text


async def test_assist_does_not_record_the_document(client: AsyncClient, auth, stub_assist) -> None:
    """The audit log keeps the instruction and a size, never the page."""
    await _enable_llm(client, auth)
    await client.post(
        "/api/v1/custom-pages/assist",
        headers=auth,
        json={"instruction": "make it blue", "html": "<p>secret marker</p>"},
    )
    entries = await client.get("/api/v1/audit-log", headers=auth)
    assert entries.status_code == 200, entries.text
    assert "secret marker" not in entries.text
    assert "make it blue" in entries.text


async def test_assist_requires_authentication(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/custom-pages/assist", json={"instruction": "x", "html": ""})
    assert resp.status_code == 401
