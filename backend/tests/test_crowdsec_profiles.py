"""The CrowdSec profile MegooPM ships: one active ban per IP.

A banned client that keeps knocking is answered 403 by the bouncer, and those
403s reach CrowdSec through the same nginx log as everything else. Scenarios
such as ``http-generic-403-bf`` count exactly that, so without a guard every
refusal re-trips the scenario and stacks another 4h ban on an IP that is
already blocked — one a minute, for as long as the client persists.

The guard is in the profile, not the scenarios: the alert is still recorded
(the operator sees the attacker persisting), only the duplicate decision is
not issued. Everything else stays at CrowdSec's stock values.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILES = REPO_ROOT / "infra" / "crowdsec" / "profiles.yaml"
TARGET = "/etc/crowdsec/profiles.yaml"
GUARD = "GetActiveDecisionsCount(Alert.GetValue()) == 0"


def _profiles() -> list[dict]:
    if not PROFILES.exists():  # pragma: no cover - partial checkout
        pytest.skip(f"{PROFILES} not present")
    return [p for p in yaml.safe_load_all(PROFILES.read_text(encoding="utf-8")) if p]


def _by_name(name: str) -> dict:
    return next(p for p in _profiles() if p["name"] == name)


@pytest.mark.parametrize("name", ["default_ip_remediation", "default_range_remediation"])
def test_a_value_with_an_active_ban_gets_no_second_one(name: str) -> None:
    (expression,) = _by_name(name)["filters"]
    assert GUARD in expression


@pytest.mark.parametrize(
    ("name", "scope"),
    [("default_ip_remediation", "Ip"), ("default_range_remediation", "Range")],
)
def test_otherwise_the_stock_profile_is_unchanged(name: str, scope: str) -> None:
    profile = _by_name(name)
    assert "Alert.Remediation == true" in profile["filters"][0]
    assert f'Alert.GetScope() == "{scope}"' in profile["filters"][0]
    assert profile["decisions"] == [{"type": "ban", "duration": "4h"}]
    assert profile["on_success"] == "break"


@pytest.mark.parametrize(
    "compose_file", ["docker-compose.yml", "docker-compose.dev.yml", "docker-compose.ha.yml"]
)
def test_every_compose_file_mounts_the_profile_read_only(compose_file: str) -> None:
    path = REPO_ROOT / compose_file
    if not path.exists():  # pragma: no cover - partial checkout
        pytest.skip(f"{compose_file} not present")
    mounts = yaml.safe_load(path.read_text(encoding="utf-8"))["services"]["crowdsec"]["volumes"]
    assert f"./infra/crowdsec/profiles.yaml:{TARGET}:ro" in mounts
