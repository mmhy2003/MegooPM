"""API tests for instance settings — the default site.

Like the other domain-schema suites these need a reachable Postgres (skipped
otherwise) and run inside one outer transaction rolled back on teardown.

The ``pg_conn`` fixture builds tables with ``Base.metadata.create_all``, which
does not run migrations, so the singleton row the migration seeds is inserted
here by a fixture instead.
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
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


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


@pytest.fixture(autouse=True)
async def seeded_settings(pg_conn) -> None:
    """`create_all` builds the table but does not run the migration's seed."""
    await pg_conn.execute(
        text("INSERT INTO instance_settings (id, default_site_mode) VALUES (1, 'not_found')")
    )


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
            email="settings-admin@example.com",
            password="adminpass123",
            role=UserRole.admin,
        )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "settings-admin@example.com", "password": "adminpass123"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture
def auth(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


# --- The singleton and its constraints -------------------------------------


async def test_migration_seeds_one_row_preserving_todays_behaviour(pg_conn) -> None:
    """A fresh instance must keep serving 404, not silently switch to a new page."""
    result = await pg_conn.execute(text("SELECT id, default_site_mode FROM instance_settings"))
    rows = result.all()
    assert len(rows) == 1
    assert rows[0].id == 1
    assert rows[0].default_site_mode == "not_found"


async def test_redirect_without_a_url_is_rejected_by_the_database(pg_conn) -> None:
    """A half-configured row would render a config that says nothing."""
    with pytest.raises(IntegrityError):
        await pg_conn.execute(
            text(
                "UPDATE instance_settings SET default_site_mode = 'redirect', "
                "default_site_redirect_url = NULL WHERE id = 1"
            )
        )


# --- Routes ----------------------------------------------------------------

CUSTOM_HTML = "<!doctype html><html><body>ban</body></html>"


async def _make_page(client: AsyncClient, auth, name: str = "Denied") -> int:
    resp = await client.post(
        "/api/v1/custom-pages", headers=auth, json={"name": name, "html": CUSTOM_HTML}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_get_returns_the_seeded_default(client: AsyncClient, auth) -> None:
    resp = await client.get("/api/v1/settings", headers=auth)
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_site_mode"] == "not_found"
    assert resp.json()["default_site_redirect_url"] is None


@pytest.mark.parametrize("mode", ["congratulations", "not_found", "no_response"])
async def test_simple_modes_round_trip(client: AsyncClient, auth, mode: str) -> None:
    resp = await client.patch(
        "/api/v1/settings/default-site", headers=auth, json={"default_site_mode": mode}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_site_mode"] == mode


async def test_redirect_mode_round_trips(client: AsyncClient, auth) -> None:
    resp = await client.patch(
        "/api/v1/settings/default-site",
        headers=auth,
        json={
            "default_site_mode": "redirect",
            "default_site_redirect_url": "https://example.com/moved",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_site_redirect_url"] == "https://example.com/moved"


async def test_custom_page_mode_round_trips(client: AsyncClient, auth) -> None:
    page_id = await _make_page(client, auth)
    resp = await client.patch(
        "/api/v1/settings/default-site",
        headers=auth,
        json={"default_site_mode": "custom_page", "default_site_page_id": page_id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_site_page_id"] == page_id


async def test_switching_mode_clears_the_previous_mode_field(client: AsyncClient, auth) -> None:
    """A stale URL would reappear in the form if the operator switched back."""
    await client.patch(
        "/api/v1/settings/default-site",
        headers=auth,
        json={
            "default_site_mode": "redirect",
            "default_site_redirect_url": "https://example.com",
        },
    )
    resp = await client.patch(
        "/api/v1/settings/default-site", headers=auth, json={"default_site_mode": "not_found"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["default_site_redirect_url"] is None


async def test_incoherent_payloads_are_rejected(client: AsyncClient, auth) -> None:
    for body in (
        {"default_site_mode": "redirect"},
        {"default_site_mode": "custom_page"},
        {"default_site_mode": "redirect", "default_site_redirect_url": "not-a-url"},
    ):
        resp = await client.patch("/api/v1/settings/default-site", headers=auth, json=body)
        assert resp.status_code == 422, (body, resp.text)


async def test_unknown_page_is_rejected(client: AsyncClient, auth) -> None:
    resp = await client.patch(
        "/api/v1/settings/default-site",
        headers=auth,
        json={"default_site_mode": "custom_page", "default_site_page_id": 9999},
    )
    assert resp.status_code == 422, resp.text


async def test_a_write_enqueues_exactly_one_reload(client: AsyncClient, auth, monkeypatch) -> None:
    calls = 0

    def _counting_reload() -> TaskEnqueued:
        nonlocal calls
        calls += 1
        return TaskEnqueued(task_id="test-reload-task", status="PENDING")

    monkeypatch.setattr(config_writes, "enqueue_nginx_reload", _counting_reload)

    resp = await client.patch(
        "/api/v1/settings/default-site", headers=auth, json={"default_site_mode": "no_response"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["X-Config-Reload-Task"] == "test-reload-task"
    assert calls == 1


async def test_endpoints_require_authentication(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/settings")).status_code == 401
    assert (
        await client.patch("/api/v1/settings/default-site", json={"default_site_mode": "not_found"})
    ).status_code == 401


async def test_the_default_site_renders_the_referenced_page(
    client: AsyncClient, auth, pg_conn
) -> None:
    """End to end: setting -> loader -> renderer, with the page's own HTML."""
    from app.services.nginx.loader import load_desired_state
    from app.services.nginx.renderer import DEFAULT_SITE_HTML, render_default_site

    page_id = await _make_page(client, auth, name="Rendered")
    await client.patch(
        "/api/v1/settings/default-site",
        headers=auth,
        json={"default_site_mode": "custom_page", "default_site_page_id": page_id},
    )

    factory = async_sessionmaker(
        bind=pg_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    async with factory() as session:
        state = await load_desired_state(session)

    assert state.default_site is not None
    assert state.default_site.mode == "custom_page"
    assert render_default_site(state)[DEFAULT_SITE_HTML] == CUSTOM_HTML


# --- LLM integration columns -----------------------------------------------


async def test_llm_is_off_on_a_fresh_instance(pg_conn) -> None:
    """Enabling by upgrade would make the proxy call a third party unasked."""
    result = await pg_conn.execute(
        text("SELECT llm_enabled, llm_model, llm_api_key_enc FROM instance_settings WHERE id = 1")
    )
    row = result.one()
    assert row.llm_enabled is False
    assert row.llm_model is None
    assert row.llm_api_key_enc is None


async def test_enabling_llm_without_a_model_is_rejected_by_the_database(pg_conn) -> None:
    """An enabled, modelless config is switched on and inert — worse than refused."""
    with pytest.raises(IntegrityError):
        await pg_conn.execute(
            text("UPDATE instance_settings SET llm_enabled = true, llm_model = NULL WHERE id = 1")
        )


async def test_a_key_may_be_absent_when_enabled(pg_conn) -> None:
    """Ollama, LM Studio and vLLM need no key; demanding one locks them out."""
    await pg_conn.execute(
        text(
            "UPDATE instance_settings SET llm_enabled = true, llm_model = 'ollama/llama3', "
            "llm_api_key_enc = NULL WHERE id = 1"
        )
    )
    result = await pg_conn.execute(text("SELECT llm_model FROM instance_settings WHERE id = 1"))
    assert result.scalar_one() == "ollama/llama3"


# --- LLM settings ----------------------------------------------------------

LLM_KEY = "sk-EXAMPLE-not-a-real-credential-1"


async def _enable_llm(client: AsyncClient, auth, **overrides) -> dict:
    body = {"llm_enabled": True, "llm_model": "gpt-4o", "llm_api_key": LLM_KEY} | overrides
    resp = await client.patch("/api/v1/settings/llm", headers=auth, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_default_site_moved_to_its_own_path(client: AsyncClient, auth) -> None:
    """The bare PATCH is gone: one route per settings group."""
    resp = await client.patch(
        "/api/v1/settings", headers=auth, json={"default_site_mode": "not_found"}
    )
    assert resp.status_code in (404, 405), resp.text


async def test_llm_settings_round_trip(client: AsyncClient, auth) -> None:
    body = await _enable_llm(client, auth, llm_api_base="https://gw.example.com")
    assert body["llm_enabled"] is True
    assert body["llm_model"] == "gpt-4o"
    assert body["llm_api_base"] == "https://gw.example.com"


async def test_the_key_is_never_returned(client: AsyncClient, auth) -> None:
    """A compromised browser session must not be able to read it back out."""
    resp = await client.patch(
        "/api/v1/settings/llm",
        headers=auth,
        json={"llm_enabled": True, "llm_model": "gpt-4o", "llm_api_key": LLM_KEY},
    )
    assert resp.status_code == 200, resp.text
    # Asserted against the raw body, not the parsed dict, so the key cannot hide
    # in a field nobody thought to check.
    assert LLM_KEY not in resp.text
    body = resp.json()
    assert body["llm_api_key_set"] is True
    assert "llm_api_key" not in body
    assert "llm_api_key_enc" not in body

    fetched = await client.get("/api/v1/settings", headers=auth)
    assert LLM_KEY not in fetched.text
    assert fetched.json()["llm_api_key_set"] is True


async def test_omitting_the_key_keeps_the_stored_one(client: AsyncClient, auth) -> None:
    """A client editing settings has no key to send back."""
    await _enable_llm(client, auth)
    resp = await client.patch(
        "/api/v1/settings/llm",
        headers=auth,
        json={"llm_enabled": True, "llm_model": "gpt-4o-mini"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["llm_api_key_set"] is True
    assert resp.json()["llm_model"] == "gpt-4o-mini"


async def test_an_explicit_null_clears_the_key(client: AsyncClient, auth) -> None:
    await _enable_llm(client, auth)
    resp = await client.patch(
        "/api/v1/settings/llm",
        headers=auth,
        json={"llm_enabled": True, "llm_model": "gpt-4o", "llm_api_key": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["llm_api_key_set"] is False


async def test_enabling_without_a_model_is_422(client: AsyncClient, auth) -> None:
    resp = await client.patch("/api/v1/settings/llm", headers=auth, json={"llm_enabled": True})
    assert resp.status_code == 422, resp.text


async def test_a_keyless_local_model_is_allowed(client: AsyncClient, auth) -> None:
    """Ollama and friends need no key; demanding one locks them out."""
    resp = await client.patch(
        "/api/v1/settings/llm",
        headers=auth,
        json={
            "llm_enabled": True,
            "llm_model": "ollama/llama3",
            "llm_api_base": "http://localhost:11434",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["llm_api_key_set"] is False


async def test_the_audit_entry_carries_no_key_material(client: AsyncClient, auth) -> None:
    await _enable_llm(client, auth)
    entries = await client.get("/api/v1/audit-log", headers=auth)
    assert entries.status_code == 200, entries.text
    assert LLM_KEY not in entries.text


async def test_llm_writes_do_not_touch_nginx(client: AsyncClient, auth, monkeypatch) -> None:
    """No rendered configuration references any of this."""
    calls = 0

    def _counting_reload() -> TaskEnqueued:
        nonlocal calls
        calls += 1
        return TaskEnqueued(task_id="test-reload-task", status="PENDING")

    monkeypatch.setattr(config_writes, "enqueue_nginx_reload", _counting_reload)
    await _enable_llm(client, auth)
    assert calls == 0


async def test_llm_endpoints_require_authentication(client: AsyncClient) -> None:
    assert (
        await client.patch("/api/v1/settings/llm", json={"llm_enabled": False})
    ).status_code == 401


# --- The probe -------------------------------------------------------------


@pytest.fixture
def stub_probe(monkeypatch):
    """Replace the LLM round trip; these tests are about the route, not litellm."""
    import app.api.routes.settings as settings_routes
    from app.services.llm import LlmCheckResult

    seen: list = []

    async def _check(config, *, timeout=30.0):
        seen.append(config)
        return LlmCheckResult(ok=True, model=config.model, reply="OK", latency_ms=7)

    monkeypatch.setattr(settings_routes, "check_connection", _check)
    return seen


async def test_probe_uses_the_stored_config(client: AsyncClient, auth, stub_probe) -> None:
    await _enable_llm(client, auth, llm_api_base="https://gw.example.com")
    resp = await client.post("/api/v1/settings/llm/test", headers=auth, json={})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "ok": True,
        "model": "gpt-4o",
        "reply": "OK",
        "error": "",
        "latency_ms": 7,
    }
    assert stub_probe[0].model == "gpt-4o"
    assert stub_probe[0].api_key == LLM_KEY
    assert stub_probe[0].api_base == "https://gw.example.com"


async def test_probe_accepts_overrides_so_a_key_can_be_checked_before_saving(
    client: AsyncClient, auth, stub_probe
) -> None:
    await _enable_llm(client, auth)
    resp = await client.post(
        "/api/v1/settings/llm/test",
        headers=auth,
        json={"model": "gpt-4o-mini", "api_key": "sk-unsaved-value-abcdefghijkl"},
    )
    assert resp.status_code == 200, resp.text
    assert stub_probe[0].model == "gpt-4o-mini"
    assert stub_probe[0].api_key == "sk-unsaved-value-abcdefghijkl"


async def test_probe_works_while_the_feature_is_switched_off(
    client: AsyncClient, auth, stub_probe
) -> None:
    """Configure, prove it works, then switch on — not the other way round."""
    await client.patch(
        "/api/v1/settings/llm",
        headers=auth,
        json={"llm_enabled": False, "llm_model": "gpt-4o", "llm_api_key": LLM_KEY},
    )
    resp = await client.post("/api/v1/settings/llm/test", headers=auth, json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


async def test_probe_without_a_model_is_422(client: AsyncClient, auth) -> None:
    """With no model there is nothing to probe."""
    resp = await client.post("/api/v1/settings/llm/test", headers=auth, json={})
    assert resp.status_code == 422, resp.text


async def test_a_failed_probe_is_200_with_ok_false(client: AsyncClient, auth, monkeypatch) -> None:
    """The API call succeeded; the upstream did not. An error status would make
    a working endpoint indistinguishable from a broken one in monitoring."""
    import app.api.routes.settings as settings_routes
    from app.services.llm import LlmCheckResult

    async def _check(config, *, timeout=30.0):
        return LlmCheckResult(ok=False, model=config.model, error="401 unauthorized")

    monkeypatch.setattr(settings_routes, "check_connection", _check)

    await _enable_llm(client, auth)
    resp = await client.post("/api/v1/settings/llm/test", headers=auth, json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is False
    assert resp.json()["error"] == "401 unauthorized"


async def test_probe_requires_authentication(client: AsyncClient) -> None:
    assert (await client.post("/api/v1/settings/llm/test", json={})).status_code == 401


# --- The CrowdSec ban page -------------------------------------------------


async def test_ban_page_defaults_to_the_megoopm_document(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    """An upgraded install serves a real page without anyone opening Settings."""
    body = (await client.get("/api/v1/settings", headers=auth)).json()
    assert body["crowdsec_ban_mode"] == "megoopm"
    assert body["crowdsec_ban_page_id"] is None


async def test_ban_page_can_be_set_to_none(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    """The deliberate choice to keep today's bare 403."""
    resp = await client.patch(
        "/api/v1/settings/ban-page", json={"crowdsec_ban_mode": "none"}, headers=auth
    )
    assert resp.status_code == 200
    assert resp.json()["crowdsec_ban_mode"] == "none"


async def test_ban_page_custom_mode_requires_a_page(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    resp = await client.patch(
        "/api/v1/settings/ban-page",
        json={"crowdsec_ban_mode": "custom_page"},
        headers=auth,
    )
    assert resp.status_code == 422


async def test_ban_page_rejects_a_page_that_does_not_exist(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    resp = await client.patch(
        "/api/v1/settings/ban-page",
        json={"crowdsec_ban_mode": "custom_page", "crowdsec_ban_page_id": 999999},
        headers=auth,
    )
    assert resp.status_code == 422


async def test_switching_away_from_custom_page_clears_the_reference(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    """The stored row must always describe exactly one configuration."""
    page = (
        await client.post(
            "/api/v1/custom-pages",
            json={"name": "Blocked", "html": "<h1>no</h1>"},
            headers=auth,
        )
    ).json()
    await client.patch(
        "/api/v1/settings/ban-page",
        json={"crowdsec_ban_mode": "custom_page", "crowdsec_ban_page_id": page["id"]},
        headers=auth,
    )
    body = (
        await client.patch(
            "/api/v1/settings/ban-page",
            json={"crowdsec_ban_mode": "megoopm"},
            headers=auth,
        )
    ).json()
    assert body["crowdsec_ban_page_id"] is None
