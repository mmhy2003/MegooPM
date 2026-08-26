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
    # Signing algorithm for JWTs. HS256 (HMAC) uses ``secret_key`` directly.
    jwt_algorithm: str = "HS256"
    # Short-lived access token; the client refreshes it with a refresh token.
    access_token_expire_minutes: int = 30
    # Longer-lived refresh token (default 7 days).
    refresh_token_expire_minutes: int = 60 * 24 * 7

    # Optional bootstrap admin. When both are set, ``scripts.create_user`` /
    # ``ensure_first_admin`` can seed an initial admin so the first privileged
    # user exists without a chicken-and-egg problem. Never commit real values.
    first_admin_email: str | None = None
    first_admin_password: str | None = None

    # --- Redis / Celery ---
    # Redis is the default broker and result backend for Celery. The dedicated
    # ``celery_*`` overrides let broker and backend diverge from ``redis_url``
    # (e.g. separate Redis databases) without touching the shared default.
    redis_url: str = Field(default="redis://localhost:6379/0")
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    # Run tasks synchronously in-process (no worker/broker needed). Enabled in
    # tests; must stay False in real deployments.
    celery_task_always_eager: bool = False

    # --- nginx config/reload engine ---
    # Directory (a shared volume with the nginx container) where the backend
    # writes managed ``*.conf`` files; nginx ``include``s it inside http{}.
    nginx_confd_dir: str = "/etc/nginx/conf.d"
    # Directory for TCP/UDP stream files, included from the top-level stream{}
    # context. Kept as a subdirectory of conf.d so it rides the same shared
    # volume; nginx's non-recursive ``conf.d/*.conf`` http include never picks
    # it up, so stream forwards stay out of http{}.
    nginx_stream_dir: str = "/etc/nginx/conf.d/stream"
    # Where TLS material lives on the shared certs volume; server blocks
    # reference ``{nginx_certs_dir}/{cert_id}/fullchain.pem`` and privkey.pem.
    nginx_certs_dir: str = "/etc/nginx/certs"
    # Only files with this prefix are managed by the engine; anything else in
    # conf.d (hand-placed by operators) is left untouched.
    nginx_managed_prefix: str = "megoopm-"
    # Commands the engine shells out to. Configurable so a split deployment can
    # point them at whatever reaches its nginx (wrapper, docker exec, ssh).
    nginx_test_command: str = "nginx -t"
    nginx_reload_command: str = "nginx -s reload"

    # --- TLS / ACME (Let's Encrypt) ---
    # ACME directory URL. Defaults to Let's Encrypt *staging* — safe for tests
    # and unthrottled; point at the production directory only for real certs.
    acme_directory_url: str = "https://acme-staging-v02.api.letsencrypt.org/directory"
    # Contact email registered with the ACME account (expiry warnings, ToS).
    acme_account_email: str | None = None
    # Webroot the HTTP-01 challenge files are written to. nginx serves this dir
    # at ``/.well-known/acme-challenge/`` on :80 for every managed host, so the
    # ACME server can fetch the validation token.
    acme_http_challenge_dir: str = "/etc/nginx/certs/_acme-challenge"
    # When true (dev/CI), certificates are self-signed locally instead of being
    # issued over ACME — no network or public DNS needed. Never in production.
    acme_self_signed: bool = False
    # Renew a certificate once it is within this many days of expiry. The beat
    # sweep enqueues renewals for everything inside the window.
    cert_renew_before_days: int = 30
    # How often the auto-renew sweep runs (cron hour; default 03:15 daily).
    cert_renew_sweep_hour: int = 3
    cert_renew_sweep_minute: int = 15

    # --- CrowdSec (MEG-22) ---
    # Base URL of the CrowdSec Local API (LAPI). The backend reads decisions /
    # alerts and pushes manual decisions here.
    crowdsec_lapi_url: str = "http://crowdsec:8080"
    # Bouncer API key (``X-Api-Key``) used to read active decisions from LAPI.
    # This is the same key handed to the nginx bouncer. None disables the read
    # path (endpoints report the integration as unconfigured rather than 500).
    crowdsec_lapi_key: str | None = None
    # Machine credentials used to authenticate the alert read path and manual
    # decision writes (bouncer keys cannot write). Registered with
    # ``cscli machines add``. Both must be set to enable the write/alert paths.
    crowdsec_machine_id: str | None = None
    crowdsec_machine_password: str | None = None
    # Origin tag stamped on decisions/alerts this backend creates, so operator
    # actions are distinguishable from engine-generated ones in CrowdSec.
    crowdsec_origin: str = "megoopm"
    # Per-request timeout (seconds) for LAPI calls.
    crowdsec_timeout_seconds: float = 5.0

    # --- CORS ---
    # Comma-separated origins, or "*" for all. NoDecode disables
    # pydantic-settings' JSON pre-parsing so the validator below handles the
    # raw comma-separated string form.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])

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
    def effective_celery_broker_url(self) -> str:
        """Celery broker URL, falling back to ``redis_url``."""
        return self.celery_broker_url or self.redis_url

    @property
    def effective_celery_result_backend(self) -> str:
        """Celery result backend URL, falling back to ``redis_url``."""
        return self.celery_result_backend or self.redis_url

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
