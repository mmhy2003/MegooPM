"""Application configuration, loaded from the environment via pydantic-settings.

All settings are env-driven. Names are read case-insensitively and may be
supplied through a local ``.env`` file (see ``.env.example``). Prefer setting
real environment variables in deployed environments.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the MegooPM backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Service metadata ---
    project_name: str = "MegooPM"
    environment: str = "development"
    debug: bool = False

    # --- API ---
    api_v1_prefix: str = "/api/v1"

    # --- Database ---
    # Async SQLAlchemy URL, e.g. postgresql+asyncpg://user:pass@host:5432/db
    database_url: str = Field(
        default="postgresql+asyncpg://megoopm:megoopm@localhost:5432/megoopm",
    )
    db_echo: bool = False

    # --- Security ---
    secret_key: str = Field(default="change-me-in-production")
    access_token_expire_minutes: int = 60 * 24

    # --- CORS ---
    # Comma-separated origins, or "*" for all. NoDecode disables
    # pydantic-settings' JSON pre-parsing so the validator below handles the
    # raw comma-separated string form.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["*"]
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Allow CORS origins to be given as a comma-separated string."""
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            return [origin.strip() for origin in stripped.split(",") if origin.strip()]
        return value

    @property
    def sync_database_url(self) -> str:
        """A synchronous driver URL, handy for tooling that is not async-aware."""
        return self.database_url.replace("+asyncpg", "").replace(
            "postgresql://", "postgresql+psycopg://"
        )


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance.

    Cached so the environment is parsed once per process. Tests can clear the
    cache via ``get_settings.cache_clear()``.
    """
    return Settings()


settings = get_settings()
