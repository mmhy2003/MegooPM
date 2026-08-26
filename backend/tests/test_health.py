"""Smoke test for the liveness endpoint."""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_returns_ok(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"]
    assert "environment" in body


async def test_settings_load_from_environment(monkeypatch) -> None:
    """Settings are env-driven via pydantic-settings."""
    from app.core.config import Settings

    monkeypatch.setenv("PROJECT_NAME", "OverriddenName")
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")

    settings = Settings()

    assert settings.project_name == "OverriddenName"
    assert settings.cors_origins == ["http://a.test", "http://b.test"]
