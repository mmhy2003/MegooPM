"""infra/crowdsec/start.sh: CrowdSec starts when its hub cannot be reached.

The image's own entrypoint refreshes the hub and reinstalls items on every
start, under ``set -e``. While CrowdSec's servers refused or could not be
reached from a deployment, every restart was a crash loop, and with the
bouncer failing closed every protected host denied traffic. The wrapper
rewrites those hub steps before running the stock script.

The stock script is vendored under ``fixtures/`` for the image version the
compose files pin, so a version bump has to refresh it, and these tests then
say whether the rewrite still matches what the new script calls.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "infra" / "crowdsec" / "start.sh"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "crowdsec"
COMPOSE_FILES = ["docker-compose.yml", "docker-compose.ha.yml", "docker-compose.dev.yml"]
ENTRYPOINT = ["/bin/bash", "/megoopm-start.sh"]
HUB_STEP = re.compile(
    r"^ *(cscli_if_clean|run_hub_update_if_from_volume|run_hub_upgrade_if_from_volume) "
)

BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash is not installed")

# The shape of the stock script that matters: set -e, and hub steps that fail.
FAILING_HUB = """\
#!/bin/bash
set -e
shopt -s inherit_errexit
cscli_if_clean() { echo "cscli $*"; return 1; }
run_hub_update_if_from_volume() { echo "hub update"; return 1; }
prepare_hub() {
    run_hub_update_if_from_volume || true
    cscli_if_clean parsers install crowdsecurity/docker-logs
    if [ "$COLLECTIONS" != "" ]; then
        cscli_if_clean collections install "$COLLECTIONS"
    fi
}
COLLECTIONS="crowdsecurity/nginx"
prepare_hub
echo "crowdsec started"
"""


def _compose(name: str) -> dict:
    path = REPO_ROOT / name
    if not path.exists():  # pragma: no cover - partial checkout
        pytest.skip(f"{name} not present at {REPO_ROOT}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))["services"]["crowdsec"]


def _image_version() -> str:
    return _compose("docker-compose.yml")["image"].rsplit(":", 1)[1]


def _run(stock: Path, tmp_path: Path, *, mode: str, exec_: str | None = None):
    if not WRAPPER.exists():  # pragma: no cover - partial checkout
        pytest.skip(f"{WRAPPER} not present")
    env = {
        **os.environ,
        "MEGOOPM_STOCK_START": str(stock),
        "MEGOOPM_PATCHED_START": str(tmp_path / "patched.sh"),
        "MEGOOPM_HUB_MODE": mode,
    }
    if exec_ is not None:
        env["MEGOOPM_START_EXEC"] = exec_
    return subprocess.run(  # noqa: S603 - fixed argv
        [BASH, str(WRAPPER)], env=env, capture_output=True, text=True, timeout=30, check=False
    )


def _stock(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "docker_start.sh"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


# --- behaviour --------------------------------------------------------------------


@needs_bash
def test_the_stock_shape_dies_on_a_failed_hub_step(tmp_path: Path) -> None:
    """The control: without the wrapper this is the crash loop."""
    stock = _stock(tmp_path, FAILING_HUB)
    done = subprocess.run(  # noqa: S603 - fixed argv
        [BASH, str(stock)], capture_output=True, text=True, timeout=30, check=False
    )
    assert done.returncode != 0
    assert "crowdsec started" not in done.stdout


@needs_bash
@pytest.mark.parametrize("mode", ["online", "offline"])
def test_crowdsec_starts_when_hub_steps_fail(tmp_path: Path, mode: str) -> None:
    done = _run(_stock(tmp_path, FAILING_HUB), tmp_path, mode=mode)
    assert done.returncode == 0, done.stderr
    assert "crowdsec started" in done.stdout


@needs_bash
def test_offline_skips_hub_steps_instead_of_waiting_them_out(tmp_path: Path) -> None:
    # Each step would wait out its own network timeout before failing.
    done = _run(_stock(tmp_path, FAILING_HUB), tmp_path, mode="offline")
    assert "hub update" not in done.stdout and "cscli " not in done.stdout
    assert "unreachable" in done.stderr


@needs_bash
def test_online_still_runs_hub_steps(tmp_path: Path) -> None:
    done = _run(_stock(tmp_path, FAILING_HUB), tmp_path, mode="online")
    assert "hub update" in done.stdout
    assert "cscli parsers install crowdsecurity/docker-logs" in done.stdout
    assert "cscli collections install crowdsecurity/nginx" in done.stdout


@needs_bash
def test_a_script_without_hub_steps_runs_unchanged(tmp_path: Path) -> None:
    """A future image may rename the steps: then run it as shipped, loudly."""
    stock = _stock(tmp_path, '#!/bin/bash\nset -e\necho "crowdsec started"\n')
    done = _run(stock, tmp_path, mode="offline")
    assert done.returncode == 0 and "crowdsec started" in done.stdout
    assert "unchanged" in done.stderr


# --- against the image's real script ---------------------------------------------


def test_the_vendored_stock_script_matches_the_pinned_image() -> None:
    """Bumping the image means refreshing the fixture, and these tests with it:

    docker run --rm --entrypoint cat crowdsecurity/crowdsec:<tag> /docker_start.sh
    """
    assert (FIXTURES / f"docker_start-{_image_version()}.sh").exists()


def _real_stock() -> Path:
    path = FIXTURES / f"docker_start-{_image_version()}.sh"
    if not path.exists():
        pytest.fail(f"missing {path}: see test_the_vendored_stock_script_matches_the_pinned_image")
    return path


def test_the_real_script_has_the_hub_steps_the_wrapper_rewrites() -> None:
    text = _real_stock().read_text(encoding="utf-8")
    calls = [line for line in text.splitlines() if HUB_STEP.match(line)]
    assert any("run_hub_update_if_from_volume" in c for c in calls)
    assert any("run_hub_upgrade_if_from_volume" in c for c in calls)
    assert any("docker-logs" in c for c in calls)
    assert any("COLLECTIONS" in c for c in calls)


@needs_bash
@pytest.mark.parametrize("mode", ["online", "offline"])
def test_the_real_script_rewrites_cleanly(tmp_path: Path, mode: str) -> None:
    done = _run(_real_stock(), tmp_path, mode=mode, exec_="true")
    assert done.returncode == 0, done.stderr
    patched = (tmp_path / "patched.sh").read_text(encoding="utf-8")

    # extglob: the stock script turns it on with shopt before using it, which
    # a parse-only run never executes.
    syntax = subprocess.run(  # noqa: S603 - fixed argv
        [BASH, "-O", "extglob", "-n", str(tmp_path / "patched.sh")],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr

    calls = [line for line in patched.splitlines() if HUB_STEP.match(line)]
    if mode == "offline":
        assert calls == []
    else:
        assert calls and all("|| echo" in c for c in calls)
    # Only the call sites change: definitions and everything else stay.
    assert "cscli_if_clean() {" in patched
    assert patched.rstrip().endswith("exec crowdsec $ARGS")


# --- wiring -----------------------------------------------------------------------


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
def test_compose_runs_crowdsec_through_the_wrapper(compose_file: str) -> None:
    service = _compose(compose_file)
    assert service["entrypoint"] == ENTRYPOINT
    assert "./infra/crowdsec/start.sh:/megoopm-start.sh:ro" in service["volumes"]


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
def test_every_compose_file_pins_the_same_image(compose_file: str) -> None:
    # One vendored stock script covers all three only if they agree.
    assert _compose(compose_file)["image"] == _compose("docker-compose.yml")["image"]
