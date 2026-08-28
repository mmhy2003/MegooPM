"""``docker compose config`` must succeed for every shipped compose file.

Runs only where the ``docker`` CLI is available (CI runners have it; the
backend test container does not) — otherwise the module is skipped, never
silently green.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_PROD = {
    "SECRET_KEY": "x" * 32,
    "POSTGRES_PASSWORD": "pw",
    "CROWDSEC_BOUNCER_KEY": "bouncer",
    "CROWDSEC_REGISTRATION_TOKEN": "r" * 32,
    "NGINX_RELOAD_TOKEN": "token",
    "NEXT_PUBLIC_API_BASE_URL": "http://localhost:8000",
}
REQUIRED_HA = {
    "NODE_ID": "node-test",
    "SHARED_DATA_PATH": "/tmp/megoopm-shared",
    "DATABASE_URL": "postgresql+asyncpg://u:p@db:5432/megoopm",
    "REDIS_URL": "redis://redis:6379/0",
    "CROWDSEC_LAPI_URL": "http://crowdsec:8080",
    "CROWDSEC_APPSEC_URL": "http://crowdsec:7422",
    "SECRET_KEY": "x" * 32,
    "CROWDSEC_BOUNCER_KEY": "bouncer",
    "CROWDSEC_REGISTRATION_TOKEN": "r" * 32,
    "NGINX_RELOAD_TOKEN": "token",
    "NEXT_PUBLIC_API_BASE_URL": "http://localhost:8000",
    "COMPOSE_PROFILES": "control-plane,scheduler",
}


def _compose_available() -> bool:
    """True when ``docker compose`` is installed and answers (plugin present)."""
    if shutil.which("docker") is None:
        return False
    try:
        probe = subprocess.run(  # noqa: S603 - fixed argv
            ["docker", "compose", "version"], capture_output=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


pytestmark = pytest.mark.skipif(not _compose_available(), reason="docker compose not available")


def _config(
    compose_file: str, env: dict[str, str], tmp_path: Path
) -> subprocess.CompletedProcess[str]:
    env_file = tmp_path / ".env"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8")
    return subprocess.run(  # noqa: S603 - fixed argv, no user input
        ["docker", "compose", "-f", compose_file, "--env-file", str(env_file), "config", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize(
    ("compose_file", "env"),
    [
        ("docker-compose.dev.yml", {}),
        ("docker-compose.yml", REQUIRED_PROD),
        ("docker-compose.ha.yml", REQUIRED_HA),
    ],
)
def test_compose_file_is_valid(compose_file: str, env: dict[str, str], tmp_path: Path) -> None:
    result = _config(compose_file, env, tmp_path)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("compose_file", "env", "missing"),
    [
        ("docker-compose.yml", REQUIRED_PROD, "NGINX_RELOAD_TOKEN"),
        ("docker-compose.ha.yml", REQUIRED_HA, "NODE_ID"),
        ("docker-compose.ha.yml", REQUIRED_HA, "SHARED_DATA_PATH"),
    ],
)
def test_production_files_refuse_missing_required_vars(
    compose_file: str, env: dict[str, str], missing: str, tmp_path: Path
) -> None:
    result = _config(compose_file, {k: v for k, v in env.items() if k != missing}, tmp_path)
    assert result.returncode != 0
    assert missing in result.stderr
