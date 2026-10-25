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


# --- Security → Updates: the app-owned config override --------------------------

ALL_COMPOSE = ["docker-compose.yml", "docker-compose.dev.yml", "docker-compose.ha.yml"]
CONFIG_LOCAL_TARGET = "/etc/crowdsec/config.yaml.local"


def _sources_for(mounts: list, target: str) -> list[str]:
    """The source (host path, volume, or volume+subpath) of every mount at ``target``."""
    out = []
    for m in mounts:
        if isinstance(m, str):
            # `${SHARED_DATA_PATH:?}/x:/etc/x:ro` — the source can carry a ":" of
            # its own, so find the target segment and take everything before it.
            parts = m.split(":")
            if target in parts:
                out.append(":".join(parts[: parts.index(target)]))
        elif m.get("target") == target:
            out.append(f"{m.get('source')}:{(m.get('volume') or {}).get('subpath', '')}")
    return out


@pytest.mark.parametrize("compose_file", ALL_COMPOSE)
def test_crowdsec_mounts_the_app_owned_config_local(compose_file: str) -> None:
    services = _services(compose_file)
    sources = _sources_for(services["crowdsec"]["volumes"], CONFIG_LOCAL_TARGET)
    assert len(sources) == 1, sources
    # The app-owned file under the data path, never the repo template.
    assert "infra/crowdsec" not in sources[0]
    assert "crowdsec/config.yaml.local" in sources[0]


@pytest.mark.parametrize("compose_file", ALL_COMPOSE)
def test_data_init_seeds_config_local(compose_file: str) -> None:
    services = _services(compose_file)
    command = " ".join(services["data-init"]["command"])
    assert "config.yaml.local" in command
    # Seeded from the repo template, mounted read-only for that purpose.
    assert any(v.endswith("/seed/config.yaml.local:ro") for v in services["data-init"]["volumes"])


@pytest.mark.parametrize("compose_file", ALL_COMPOSE)
def test_crowdsec_uses_wal(compose_file: str) -> None:
    services = _services(compose_file)
    assert services["crowdsec"]["environment"]["USE_WAL"] == "true"


def test_dev_worker_can_reach_the_socket_and_the_data_path() -> None:
    services = _services("docker-compose.dev.yml")
    volumes = services["worker"]["volumes"]
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in volumes
    assert "app_data:/data" in volumes


@pytest.mark.parametrize("compose_file", ALL_COMPOSE)
def test_worker_carries_the_docker_socket_group(compose_file: str) -> None:
    # uid 1000 + a root:docker 0660 socket = EACCES on every reload without this.
    services = _services(compose_file)
    groups = services["worker"].get("group_add") or []
    assert any("DOCKER_GID" in str(g) for g in groups), groups


# --- Security → Settings: the WAF's request-body limit ------------------------------

WAF_TARGET = "/etc/crowdsec/appsec-configs/megoopm-body-size.yaml"


@pytest.mark.parametrize("compose_file", ALL_COMPOSE)
def test_crowdsec_mounts_the_body_size_config_read_only(compose_file: str) -> None:
    """One FILE among hub-installed AppSec configs, from the data path."""
    mounts = _services(compose_file)["crowdsec"]["volumes"]
    sources = _sources_for(mounts, WAF_TARGET)
    assert len(sources) == 1, sources
    assert "infra/crowdsec" not in sources[0]
    assert "crowdsec/appsec/megoopm-body-size.yaml" in sources[0]
    for mount in mounts:
        if isinstance(mount, str) and WAF_TARGET in mount:
            assert mount.endswith(":ro")
        elif isinstance(mount, dict) and mount.get("target") == WAF_TARGET:
            assert mount.get("read_only") is True


@pytest.mark.parametrize("compose_file", ALL_COMPOSE)
def test_data_init_seeds_the_body_size_config(compose_file: str) -> None:
    """A missing source makes Docker create a DIRECTORY, and CrowdSec won't start.

    An upgraded install has no crowdsec/appsec/ yet, so data-init creates it.
    """
    services = _services(compose_file)
    command = " ".join(services["data-init"]["command"])
    assert "mkdir -p" in command and "/data/crowdsec/appsec" in command
    assert "/data/crowdsec/appsec/megoopm-body-size.yaml" in command
    assert any(
        v.endswith("/seed/appsec-body-size.yaml:ro") for v in services["data-init"]["volumes"]
    )


@pytest.mark.parametrize("compose_file", ALL_COMPOSE)
@pytest.mark.parametrize("service", ["backend", "worker"])
def test_the_appsec_url_reaches_the_app(compose_file: str, service: str) -> None:
    """The Settings tab says when AppSec is not wired; it can only know from this."""
    env = _services(compose_file)[service].get("environment", {})
    assert "CROWDSEC_APPSEC_URL" in env


def test_appsec_loads_megoopm_body_size_last() -> None:
    """Later AppSec configs win; the hub default must not override the limit."""
    acquis = yaml.safe_load(
        (REPO_ROOT / "infra" / "crowdsec" / "acquis" / "appsec.yaml").read_text(encoding="utf-8")
    )
    assert acquis["appsec_configs"] == ["crowdsecurity/appsec-default", "megoopm/body-size"]
    assert "appsec_config" not in acquis


def test_the_bouncer_e2e_mounts_every_appsec_config_the_acquisition_names() -> None:
    """infra/nginx/tests/bouncer-phase.sh mounts the real acquisition file.

    That file names megoopm/body-size; a CrowdSec without the config cannot
    resolve it, the AppSec datasource fails to load, and the script fails
    before testing anything.
    """
    script = (REPO_ROOT / "infra" / "nginx" / "tests" / "bouncer-phase.sh").read_text(
        encoding="utf-8"
    )
    assert "infra/crowdsec/acquis/appsec.yaml:/etc/crowdsec/acquis.d/appsec.yaml" in script
    assert f"infra/crowdsec/appsec-body-size.yaml:{WAF_TARGET}:ro" in script


# --- big bodies must not time out into a denial ---------------------------------------
#
# Measured on CrowdSec 1.8.1 + lua-cs-bouncer v1.0.19: with the bouncer's
# defaults (APPSEC_PROCESS_TIMEOUT=500 ms) checking a 100 MB body takes longer
# than that, the bouncer fails closed (APPSEC_FAILURE_ACTION=deny) and the
# upload gets the ban page — served with status 200, which hides it. AppSec's
# own body_read_timeout (default 1 s) does the same from the other side.

UPLOAD_TIMEOUT_MS = 30_000


def _bouncer_conf() -> dict[str, str]:
    text = (REPO_ROOT / "infra" / "nginx" / "crowdsec-bouncer.conf").read_text(encoding="utf-8")
    pairs = (
        line.split("=", 1)
        for line in text.splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )
    return {k.strip(): v.strip() for k, v in pairs}


@pytest.mark.parametrize("key", ["APPSEC_SEND_TIMEOUT", "APPSEC_PROCESS_TIMEOUT"])
def test_the_bouncer_waits_long_enough_for_a_big_body(key: str) -> None:
    conf = _bouncer_conf()
    assert key in conf, f"{key} unset: v1.0.19's default is far too short for uploads"
    assert int(conf[key]) >= UPLOAD_TIMEOUT_MS


def test_appsec_reads_a_big_body_without_its_one_second_deadline() -> None:
    acquis = yaml.safe_load(
        (REPO_ROOT / "infra" / "crowdsec" / "acquis" / "appsec.yaml").read_text(encoding="utf-8")
    )
    assert acquis.get("body_read_timeout") == "30s"
