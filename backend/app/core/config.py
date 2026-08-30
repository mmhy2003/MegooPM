"""Application configuration, loaded from the environment via pydantic-settings.

All settings are env-driven. Names are read case-insensitively and may be
supplied through a local ``.env`` file (see ``.env.example``). Prefer setting
real environment variables in deployed environments.
"""

from __future__ import annotations

import socket
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator, model_validator
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

    # --- Shared state root (HA / MEG-35) ---
    # Single shared location (NFS / shared volume) holding *all* mutable state
    # so any node can serve traffic. In an HA deployment every app node mounts
    # this at the same path. The nginx/certs/ACME paths below default to
    # subdirectories of it; set them explicitly to override that derivation.
    shared_data_dir: str = "/data"

    # --- nginx config/reload engine ---
    # Directory (a shared volume with the nginx container) where the backend
    # writes managed ``*.conf`` files; nginx ``include``s it inside http{}.
    # Defaults to ``{shared_data_dir}/nginx/conf.d`` when left unset.
    nginx_confd_dir: str | None = None
    # Directory for TCP/UDP stream files, included from the top-level stream{}
    # context. Kept as a subdirectory of conf.d so it rides the same shared
    # volume; nginx's non-recursive ``conf.d/*.conf`` http include never picks
    # it up, so stream forwards stay out of http{}. Defaults to
    # ``{nginx_confd_dir}/stream``.
    nginx_stream_dir: str | None = None
    # Where TLS material lives on the shared certs volume; server blocks
    # reference ``{nginx_certs_dir}/{cert_id}/fullchain.pem`` and privkey.pem.
    # Defaults to ``{shared_data_dir}/certs``.
    nginx_certs_dir: str | None = None
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
    # ACME server can fetch the validation token. Defaults to
    # ``{nginx_certs_dir}/_acme-challenge`` (on the shared volume so any node
    # can answer the challenge).
    acme_http_challenge_dir: str | None = None
    # When true (dev/CI), certificates are self-signed locally instead of being
    # issued over ACME — no network or public DNS needed. Never in production.
    acme_self_signed: bool = False
    # DNS-01: after the provider publishes the ``_acme-challenge`` TXT record,
    # poll the zone's authoritative nameservers until they all serve it (or give
    # up) before asking the ACME server to validate. Prevents the common
    # "record not propagated yet" failure and the rate-limit hits it causes.
    acme_dns_propagation_timeout_seconds: int = 120
    acme_dns_propagation_interval_seconds: int = 5
    # Extra grace period after every nameserver serves the record, before the
    # challenge is answered. Anycast providers (Cloudflare, Route 53, ...) answer
    # from the nearest PoP, so the poll above proves one vantage point while the
    # ACME server validates from several; answering at once fails with "During
    # secondary validation: Incorrect TXT record ...". certbot's DNS plugins
    # default to the same 10 s.
    acme_dns_propagation_settle_seconds: int = 10
    # Renew a certificate once it is within this many days of expiry. The beat
    # sweep enqueues renewals for everything inside the window.
    cert_renew_before_days: int = 30
    # How often the auto-renew sweep runs (cron hour; default 03:15 daily).
    cert_renew_sweep_hour: int = 3
    cert_renew_sweep_minute: int = 15
    # Minimum gap between two *effective* renewal sweeps, cluster-wide. Every node
    # runs beat, so the daily sweep is emitted once per node; a leader lock only
    # excludes concurrent runs, and these are sequential. Claiming the sweep
    # against this window makes it run once per period however many beats fire it.
    # Half the schedule: long enough to swallow clock skew between nodes, short
    # enough never to suppress a genuine next day.
    cert_renew_sweep_min_interval_seconds: float = 12 * 3600

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
    # LAPI auto-registration token (``api.server.auto_registration.token`` on the
    # CrowdSec side, >= 32 chars). Sent with the self-registration request so the
    # new machine is validated immediately — no ``cscli machines validate``.
    # Without it the machine still registers but stays pending validation.
    crowdsec_registration_token: str | None = None
    # Origin tag stamped on decisions/alerts this backend creates, so operator
    # actions are distinguishable from engine-generated ones in CrowdSec.
    crowdsec_origin: str = "megoopm"
    # Per-request timeout (seconds) for LAPI calls.
    crowdsec_timeout_seconds: float = 5.0

    # --- High Availability (MEG-35) ---
    # Turn on cross-node coordination for multi-node deployments. When True the
    # nginx apply path takes a Postgres advisory lock (safe against concurrent
    # applies from other nodes), bumps a shared ``config_version``, and fans a
    # reconcile out to every node so each reloads its local nginx; the periodic
    # sweeps (cert renewal, etc.) run under a cluster-wide leader lock. When
    # False the engine keeps its single-host behaviour (local file lock only) —
    # so single-node deployments and tests need no Postgres coordination.
    ha_enabled: bool = False
    # Stable identifier for this node, stamped on config-version bumps for
    # observability. Defaults to the hostname.
    node_id: str | None = None
    # Node-LOCAL run directory for coordination lock files. Only used on the
    # non-Postgres fallback lock path (single host / tests); on a real HA
    # cluster the locks are Postgres advisory locks and this is unused.
    ha_lock_dir: str = "/var/run/megoopm"
    # Node-LOCAL marker (NOT on the shared volume) recording the last config
    # version this node reloaded nginx for. Each node compares it to the shared
    # version to decide whether a reload is due.
    nginx_reload_marker_path: str = "/var/run/megoopm/nginx-config.version"
    # How often (seconds) each node reconciles its local nginx against the
    # shared config version. Every node runs its own beat emitting this onto its
    # own queue, so this is the hard upper bound on how long a node can serve a
    # stale config after a change — including a node that was down or newly
    # added, for which the push fan-out never fired.
    ha_reconcile_interval_seconds: float = 30.0
    # TTL stamped on reconcile messages. A reconcile is only meaningful while it
    # is current: the version check makes a stale one a no-op, but without a TTL
    # a node that is offline for hours wakes to a backlog of them. Defaults to
    # three intervals — long enough to survive a slow node, short enough to keep
    # the queue bounded.
    ha_reconcile_expires_seconds: float | None = None
    # Multiple of the reconcile interval within which a node must have checked in
    # to still receive pushed reconciles. Beyond it a node is presumed gone and
    # drops out of the fan-out, so a decommissioned node's queue stops growing;
    # if it ever returns, its own beat converges it.
    ha_node_liveness_multiplier: float = 4.0

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

    @model_validator(mode="after")
    def _derive_shared_paths(self) -> Settings:
        """Fill any unset state paths from ``shared_data_dir``.

        Only paths left at their ``None`` default are derived, so an explicit
        env var (or the legacy single-host ``/etc/nginx/...`` values) always
        wins. This keeps existing deployments byte-for-byte compatible while
        giving fresh/HA deployments a single shared root out of the box.
        """
        root = self.shared_data_dir.rstrip("/")
        if self.nginx_confd_dir is None:
            self.nginx_confd_dir = f"{root}/nginx/conf.d"
        if self.nginx_stream_dir is None:
            self.nginx_stream_dir = f"{self.nginx_confd_dir.rstrip('/')}/stream"
        if self.nginx_certs_dir is None:
            self.nginx_certs_dir = f"{root}/certs"
        if self.acme_http_challenge_dir is None:
            self.acme_http_challenge_dir = f"{self.nginx_certs_dir.rstrip('/')}/_acme-challenge"
        return self

    @property
    def effective_node_id(self) -> str:
        """This node's identifier, falling back to the hostname."""
        return self.node_id or socket.gethostname()

    @property
    def effective_reconcile_expires_seconds(self) -> float:
        """TTL for a reconcile message, defaulting to three reconcile intervals."""
        if self.ha_reconcile_expires_seconds is not None:
            return self.ha_reconcile_expires_seconds
        return self.ha_reconcile_interval_seconds * 3

    @property
    def node_liveness_window_seconds(self) -> float:
        """How recently a node must have reconciled to still be pushed to."""
        return self.ha_reconcile_interval_seconds * self.ha_node_liveness_multiplier

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
