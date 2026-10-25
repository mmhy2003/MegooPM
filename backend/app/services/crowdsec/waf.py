"""The WAF's request-body limit: MegooPM's AppSec config, and applying it.

CrowdSec from 1.7 on buffers each request body in memory before its AppSec
rules run, up to 10 MB by default, and by default *refuses* anything larger
with the ban remediation. Behind a file-sharing app that means every large
upload fails. The limit and the over-limit action are hook helpers that run
when an AppSec config loads (``on_load``), so MegooPM owns one small config,
``megoopm/body-size``, listed last in ``appsec_configs`` so its hooks win.

Applying it restarts CrowdSec, and protected hosts fail closed while it is
down. :func:`run_waf_apply` therefore follows the whitelist apply — test the
config, restart, wait, restore on failure — with one rung more: when CrowdSec
does not come back, the community blocklist is on, and CrowdSec's own
firewall is refusing this address, it switches the blocklist off and
restarts again. CrowdSec treats a central-API refusal as fatal at startup;
without the blocklist it needs no central API, so it comes back with the new
limit in force instead of leaving every host denying traffic until the block
lifts.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from app.models.enums import AppSecBodyAction
from app.services.crowdsec.capi import render_config_local
from app.services.crowdsec.reload import CrowdSecReloadError, ExecResult
from app.services.crowdsec.whitelists import config_test_error

WAF_CONFIG_NAME = "megoopm/body-size"
DEFAULT_MAX_BODY_BYTES = 10 * 1024 * 1024
DEFAULT_ACTION = AppSecBodyAction.partial

_HEADER = """\
# Managed by MegooPM — Security → Settings rewrites this file. Edit
# infra/crowdsec/appsec-body-size.yaml in the repo for the seed.
#
# The WAF's request-body limit. Loaded after crowdsecurity/appsec-default
# (see infra/crowdsec/acquis/appsec.yaml), so these hooks run last and win.
"""


def render_body_size_config(*, max_body_bytes: int, action: AppSecBodyAction) -> str:
    return (
        f"{_HEADER}name: {WAF_CONFIG_NAME}\n"
        "on_load:\n"
        "  - apply:\n"
        f"      - SetMaxBodySize({max_body_bytes})\n"
        f'      - SetBodySizeExceededAction("{action.value}")\n'
    )


@dataclass(frozen=True, slots=True)
class WafApplyResult:
    ok: bool
    error: str | None
    restarted: bool
    blocklist_switched_off: bool

    def as_dict(self) -> dict:
        return asdict(self)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _write(path: Path, content: str) -> None:
    # In place, never replaced: the container's bind mount is pinned to the
    # inode it saw at start (see the whitelist writer for the same rule).
    with path.open("w", encoding="utf-8") as fh:
        fh.write(content)


def run_waf_apply(
    *,
    content: str,
    path: Path,
    config_local_path: Path,
    capi_enabled: bool,
    validate: Callable[[], ExecResult],
    restart: Callable[[], None],
    healthy: Callable[[], bool],
    edge_blocked: Callable[[], bool | None],
) -> WafApplyResult:
    """Write, test, restart, verify — with the blocklist safety net, then rollback."""
    previous = _read(path)
    if content == previous:
        return WafApplyResult(True, None, False, False)

    _write(path, content)

    # Ask CrowdSec whether it would load this before making it live. An
    # unreachable container counts as "no".
    try:
        problem = config_test_error(validate())
    except CrowdSecReloadError as exc:
        _write(path, previous)
        return WafApplyResult(False, str(exc), False, False)
    if problem:
        _write(path, previous)
        return WafApplyResult(False, problem, False, False)

    try:
        restart()
    except CrowdSecReloadError as exc:
        _write(path, previous)
        return WafApplyResult(False, str(exc), False, False)
    if healthy():
        return WafApplyResult(True, None, True, False)

    # Not back. The config passed `crowdsec -t`, so with the blocklist on the
    # likeliest cause is the central API refusing us at startup. Asked only
    # now: an apply that comes back costs CrowdSec's servers no request.
    previous_local = _read(config_local_path)
    if capi_enabled and edge_blocked() is True:
        _write(config_local_path, render_config_local(capi_enabled=False))
        try:
            restart()
        except CrowdSecReloadError as exc:
            return _roll_back(path, previous, config_local_path, previous_local, restart, exc)
        if healthy():
            return WafApplyResult(True, None, True, True)

    return _roll_back(path, previous, config_local_path, previous_local, restart, None)


def _roll_back(
    path: Path,
    previous: str,
    config_local_path: Path,
    previous_local: str,
    restart: Callable[[], None],
    cause: CrowdSecReloadError | None,
) -> WafApplyResult:
    """Put both files back as they were and restart onto them."""
    _write(path, previous)
    if _read(config_local_path) != previous_local:
        _write(config_local_path, previous_local)
    reason = (
        f"CrowdSec could not be restarted: {cause}"
        if cause
        else ("CrowdSec did not come back after the change")
    )
    try:
        restart()
    except CrowdSecReloadError as exc:
        return WafApplyResult(
            False, f"{reason}, and the rollback restart also failed: {exc}", True, False
        )
    return WafApplyResult(False, f"{reason}. The previous limit was restored.", True, False)


__all__ = [
    "DEFAULT_ACTION",
    "DEFAULT_MAX_BODY_BYTES",
    "WAF_CONFIG_NAME",
    "WafApplyResult",
    "render_body_size_config",
    "run_waf_apply",
]
