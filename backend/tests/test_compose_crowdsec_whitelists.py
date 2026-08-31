"""CrowdSec whitelist wiring in the compose files.

Reads the compose YAML directly instead of shelling out to
``docker compose config``, so these run everywhere — including the backend test
container, where the docker CLI is absent and ``test_compose_config`` skips
wholesale. The three things checked here all fail *silently* if wrong: a
directory mount masking the hub parsers, a missing seed leaving Docker to
create a directory CrowdSec cannot parse, and the docker socket landing on the
internet-facing process.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

WHITELIST_TARGET = "/etc/crowdsec/parsers/s02-enrich/99-megoopm-whitelist.yaml"


def _services(compose_file: str) -> dict:
    path = REPO_ROOT / compose_file
    if not path.exists():  # pragma: no cover - partial checkout
        pytest.skip(f"{compose_file} not present at {REPO_ROOT}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))["services"]


def _mount_targets(mounts: list) -> set[str]:
    """Targets from both the short (`src:dst:ro`) and long (mapping) forms."""
    targets: set[str] = set()
    for mount in mounts:
        if isinstance(mount, str):
            parts = mount.split(":")
            if len(parts) >= 2:
                targets.add(parts[-2] if parts[-1] in {"ro", "rw"} else parts[-1])
        elif isinstance(mount, dict) and "target" in mount:
            targets.add(mount["target"])
    return targets


@pytest.mark.parametrize("compose_file", ["docker-compose.yml", "docker-compose.ha.yml"])
def test_crowdsec_mounts_the_whitelist_as_a_single_file(compose_file: str) -> None:
    """s02-enrich already holds hub parsers (geoip enrichment among them).

    Mounting a *directory* over it would mask them and silently break
    enrichment, so the mount has to target one file.
    """
    mounts = _services(compose_file)["crowdsec"]["volumes"]
    assert WHITELIST_TARGET in _mount_targets(mounts)


@pytest.mark.parametrize("compose_file", ["docker-compose.yml", "docker-compose.ha.yml"])
def test_the_whitelist_mount_is_read_only(compose_file: str) -> None:
    mounts = _services(compose_file)["crowdsec"]["volumes"]
    for mount in mounts:
        if isinstance(mount, str) and WHITELIST_TARGET in mount:
            assert mount.endswith(":ro")
            return
        if isinstance(mount, dict) and mount.get("target") == WHITELIST_TARGET:
            assert mount.get("read_only") is True
            return
    raise AssertionError(f"no whitelist mount found in {mounts}")


@pytest.mark.parametrize("compose_file", ["docker-compose.yml", "docker-compose.ha.yml"])
def test_data_init_seeds_the_whitelist_file(compose_file: str) -> None:
    """Docker creates a DIRECTORY when a bind-mount source is missing.

    CrowdSec then fails to parse it and refuses to start, and with
    APPSEC_FAILURE_ACTION=deny on the bouncer that is a full outage on first
    boot. The seed is also what makes the file load at boot at all.
    """
    command = " ".join(_services(compose_file)["data-init"]["command"])
    assert "/data/crowdsec/whitelists" in command
    assert "megoopm.yaml" in command


@pytest.mark.parametrize("compose_file", ["docker-compose.yml", "docker-compose.ha.yml"])
def test_worker_can_reach_the_docker_socket(compose_file: str) -> None:
    mounts = _services(compose_file)["worker"]["volumes"]
    assert "/var/run/docker.sock" in _mount_targets(mounts)


@pytest.mark.parametrize("compose_file", ["docker-compose.yml", "docker-compose.ha.yml"])
def test_api_backend_cannot_reach_the_docker_socket(compose_file: str) -> None:
    """The socket is root-equivalent on the host; it stays off the web process."""
    mounts = _services(compose_file)["backend"].get("volumes", [])
    assert "/var/run/docker.sock" not in _mount_targets(mounts)


# --- environment passthrough ----------------------------------------------
#
# The compose files declare an explicit `environment:` map rather than using
# `env_file`, so a key in .env.example reaches the application ONLY if it is
# also listed here. Getting this wrong is silent: the setting keeps its default,
# `crowdsec_control_node_id` stays None, and every whitelist reports "reload not
# configured" no matter what the operator puts in .env. That is exactly what
# shipped before this test existed.

REQUIRED_ENV = (
    "CROWDSEC_CONTROL_NODE_ID",
    "CROWDSEC_CONTAINER_NAME",
)


@pytest.mark.parametrize("compose_file", ["docker-compose.yml", "docker-compose.ha.yml"])
@pytest.mark.parametrize("service", ["backend", "worker"])
@pytest.mark.parametrize("key", REQUIRED_ENV)
def test_whitelist_settings_reach_the_app(compose_file: str, service: str, key: str) -> None:
    env = _services(compose_file)[service].get("environment", {})
    assert key in env, (
        f"{key} is not passed to `{service}` in {compose_file}; the setting would "
        "silently keep its default"
    )


@pytest.mark.parametrize("compose_file", ["docker-compose.yml", "docker-compose.ha.yml"])
def test_control_node_id_is_not_given_a_default(compose_file: str) -> None:
    """Blank must stay blank.

    Defaulting it to a node name would make a cluster where that node is not the
    control plane queue applies onto a queue nobody consumes — the failure the
    "reload not configured" state exists to make visible.
    """
    env = _services(compose_file)["worker"]["environment"]
    assert env["CROWDSEC_CONTROL_NODE_ID"] == "${CROWDSEC_CONTROL_NODE_ID:-}"


# --- env example coverage --------------------------------------------------
#
# An operator configures from these files. A setting that exists in code and in
# compose but is undocumented here is one nobody will find — and the HA file is
# the one a multi-node deployment actually copies.

ENV_EXAMPLES = (".env.example", ".env.ha.example")


@pytest.mark.parametrize("env_file", ENV_EXAMPLES)
@pytest.mark.parametrize("key", REQUIRED_ENV)
def test_env_examples_document_the_whitelist_settings(env_file: str, key: str) -> None:
    path = REPO_ROOT / env_file
    if not path.exists():  # pragma: no cover - partial checkout
        pytest.skip(f"{env_file} not present at {REPO_ROOT}")
    lines = [
        line.split("=", 1)[0].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert key in lines, f"{key} is not documented in {env_file}"


@pytest.mark.parametrize("env_file", ENV_EXAMPLES)
def test_env_examples_ship_a_blank_control_node(env_file: str) -> None:
    """Shipping a value would be a footgun.

    Under HA the id names the control plane and must be identical everywhere;
    a copied-in default would have every node believe it is the control plane
    and try to restart a container it does not run.
    """
    path = REPO_ROOT / env_file
    if not path.exists():  # pragma: no cover - partial checkout
        pytest.skip(f"{env_file} not present at {REPO_ROOT}")
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("CROWDSEC_CONTROL_NODE_ID="):
            assert line.strip() == "CROWDSEC_CONTROL_NODE_ID="
            return
    raise AssertionError(f"CROWDSEC_CONTROL_NODE_ID missing from {env_file}")


@pytest.mark.parametrize("compose_file", ["docker-compose.yml", "docker-compose.ha.yml"])
def test_crowdsec_waits_for_the_seed(compose_file: str) -> None:
    """CrowdSec must not start before data-init has created the file.

    The whitelist mount is a bind mount of a FILE. Docker creates a DIRECTORY at
    the source when it is missing, and CrowdSec then refuses to start — which is
    exactly what happened in production: `data-init` came up second and hit
    "can't create /data/crowdsec/whitelists/megoopm.yaml: Is a directory".

    Seeding the file is only half the fix; the ordering is the other half.
    """
    depends = _services(compose_file)["crowdsec"].get("depends_on", {})
    assert "data-init" in depends, f"crowdsec does not wait for data-init in {compose_file}"
    assert depends["data-init"]["condition"] == "service_completed_successfully"
