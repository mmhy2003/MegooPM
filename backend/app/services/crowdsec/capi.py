"""The community-blocklist switch: what config.yaml.local says, and applying it.

CrowdSec merges ``config.yaml.local`` over ``config.yaml`` at load time —
after the image's entrypoint has deleted ``online_client`` because
``DISABLE_ONLINE_API=true``. Putting the block back here is how the blocklist
is enabled without touching the container's env. The file must be on disk
before ``cscli capi register`` runs (it refuses otherwise), and it must never
point at a missing credentials file (CrowdSec then fails to start).

The auto_registration block below is the same text as
``infra/crowdsec/config.yaml.local``, which data-init uses to seed the file.
Keep the two in step.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from app.services.crowdsec.reload import CrowdSecReloadError, ExecResult

CREDENTIALS_PATH = "/etc/crowdsec/online_api_credentials.yaml"

CMD_HAS_CREDENTIALS = [
    "sh",
    "-c",
    f"test -s {CREDENTIALS_PATH} && grep -q login {CREDENTIALS_PATH}",
]
CMD_REGISTER = ["cscli", "capi", "register", "-f", CREDENTIALS_PATH]
CMD_STATUS = ["cscli", "capi", "status"]

_BASE = """\
# Managed by MegooPM — Security → Updates rewrites this file. Edit
# infra/crowdsec/config.yaml.local in the repo for the seed.
#
# Machine auto-registration: the backend self-registers its LAPI machine with
# `POST /v1/watchers` and sends CROWDSEC_REGISTRATION_TOKEN; when it matches
# the token below and the request comes from an allowed range, LAPI validates
# the machine immediately. The token must be >= 32 characters.
api:
  server:
    auto_registration:
      enabled: true
      token: ${CROWDSEC_REGISTRATION_TOKEN}
      allowed_ranges:
        - 127.0.0.1/32
        - 10.0.0.0/8
        - 172.16.0.0/12
        - 192.168.0.0/16
"""

_ONLINE_CLIENT = f"""\
    # Community blocklist: switched on from Security → Updates.
    online_client:
      credentials_path: {CREDENTIALS_PATH}
      sharing: true
      pull:
        community: true
        blocklists: true
"""


def render_config_local(*, capi_enabled: bool) -> str:
    return _BASE + (_ONLINE_CLIENT if capi_enabled else "")


def parse_capi_status(result: ExecResult) -> bool:
    return result.exit_code == 0 and "successfully interact" in result.output


@dataclass(frozen=True, slots=True)
class CapiApplyResult:
    ok: bool
    error: str | None
    restarted: bool
    enabled: bool

    def as_dict(self) -> dict:
        return asdict(self)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _write(path: Path, content: str) -> None:
    # In place, never replaced: the container's bind mount is pinned to the
    # inode it saw at start (see the whitelist writer for the same rule).
    with path.open("w", encoding="utf-8") as fh:
        fh.write(content)


def run_capi_apply(
    *,
    enabled: bool,
    path: Path,
    exec: Callable[[list[str]], ExecResult],
    restart: Callable[[], None],
    healthy: Callable[[], bool],
) -> CapiApplyResult:
    """Write, register if needed, restart, verify — and roll back if it fails."""
    previous = _read(path)
    content = render_config_local(capi_enabled=enabled)
    if content == previous:
        return CapiApplyResult(True, None, False, enabled)

    _write(path, content)

    if enabled:
        try:
            if exec(CMD_HAS_CREDENTIALS).exit_code != 0:
                registered = exec(CMD_REGISTER)
                if registered.exit_code != 0:
                    _write(path, previous)
                    tail = registered.output.strip().splitlines()[-1:] or ["no output"]
                    return CapiApplyResult(
                        False,
                        f"Registering with CrowdSec's central API failed: {tail[0]}",
                        False,
                        False,
                    )
        except CrowdSecReloadError as exc:
            _write(path, previous)
            return CapiApplyResult(False, str(exc), False, False)

    try:
        restart()
    except CrowdSecReloadError as exc:
        _write(path, previous)
        return CapiApplyResult(False, str(exc), False, not enabled)

    verified = healthy()
    reason = "CrowdSec did not come back after the change"
    if verified and enabled:
        try:
            verified = parse_capi_status(exec(CMD_STATUS))
            reason = "cscli capi status did not confirm the connection"
        except CrowdSecReloadError as exc:
            verified, reason = False, str(exc)
    if verified:
        return CapiApplyResult(True, None, True, enabled)

    _write(path, previous)
    try:
        restart()
    except CrowdSecReloadError as exc:
        return CapiApplyResult(
            False,
            f"{reason}, and the rollback restart also failed: {exc}",
            True,
            not enabled,
        )
    return CapiApplyResult(
        False, f"{reason}. The previous configuration was restored.", True, not enabled
    )


__all__ = [
    "CMD_HAS_CREDENTIALS",
    "CMD_REGISTER",
    "CMD_STATUS",
    "CREDENTIALS_PATH",
    "CapiApplyResult",
    "parse_capi_status",
    "render_config_local",
    "run_capi_apply",
]
