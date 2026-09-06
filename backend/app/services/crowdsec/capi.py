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
class CapiStatus:
    """Whether the stored central-API credentials still work.

    ``ok`` is deliberately three-valued. ``None`` means "we could not find
    out" — the blocklist is off, or the container could not be reached — and
    that is not the same as "rejected": reporting rejection for a docker
    socket problem would send an operator re-registering working credentials.
    """

    enabled: bool
    ok: bool | None
    detail: str | None


def read_capi_enabled(path: Path) -> bool:
    """Whether ``config.yaml.local`` currently carries the online_client block.

    Read from the file rather than the settings row because the file is what
    CrowdSec loads: if the two ever disagree, this answers for the engine.
    """
    return "online_client:" in _read(path)


def check_capi_status(*, path: Path, exec: Callable[[list[str]], ExecResult]) -> CapiStatus:
    """Ask the engine whether CAPI still accepts us.

    Worth doing on its own, away from any change: CrowdSec authenticates to
    CAPI during LAPI init and treats a rejection as fatal, so credentials that
    have gone stale sit harmlessly in a running container until something
    restarts it — and then nothing starts. This is the warning before that.
    """
    if not read_capi_enabled(path):
        return CapiStatus(enabled=False, ok=None, detail=None)

    try:
        result = exec(CMD_STATUS)
    except CrowdSecReloadError as exc:
        # Unknown, not broken. A container we cannot exec into tells us
        # nothing about the credentials inside it.
        return CapiStatus(enabled=True, ok=None, detail=str(exc))

    if parse_capi_status(result):
        return CapiStatus(enabled=True, ok=True, detail=None)

    tail = result.output.strip().splitlines()[-1:] or ["cscli capi status said nothing"]
    return CapiStatus(
        enabled=True,
        ok=False,
        detail=(
            "CrowdSec's central API is refusing these credentials. The engine "
            "is running on what it loaded at start, but it will not survive a "
            f"restart until this is fixed. {tail[0]}"
        ),
    )


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


def run_capi_register(
    *,
    path: Path,
    exec: Callable[[list[str]], ExecResult],
    restart: Callable[[], None],
    healthy: Callable[[], bool],
) -> CapiApplyResult:
    """Get fresh central-API credentials, then prove they work.

    The repair for :func:`check_capi_status` reporting a rejection. Only
    useful while the container is still up: ``docker exec`` cannot reach one
    that is crash-looping, which is why the check exists to catch this before
    the next restart rather than after.

    Nothing is rolled back on failure. The credentials being replaced are the
    ones CAPI already refuses, so keeping them has no value — unlike the
    config file, which :func:`run_capi_apply` does restore.
    """
    if not read_capi_enabled(path):
        return CapiApplyResult(
            False,
            "The community blocklist is off, so there is nothing to register.",
            False,
            False,
        )

    try:
        registered = exec(CMD_REGISTER)
    except CrowdSecReloadError as exc:
        return CapiApplyResult(False, str(exc), False, True)
    if registered.exit_code != 0:
        # Never restart on credentials we know are bad: that is exactly how
        # the container ends up in a crash loop.
        tail = registered.output.strip().splitlines()[-1:] or ["no output"]
        return CapiApplyResult(
            False, f"Registering with CrowdSec's central API failed: {tail[0]}", False, True
        )

    try:
        restart()
    except CrowdSecReloadError as exc:
        return CapiApplyResult(False, str(exc), False, True)

    if not healthy():
        return CapiApplyResult(
            False,
            "CrowdSec did not come back after registering. Check its logs.",
            True,
            True,
        )

    # Confirm after the restart, not before: before, cscli would only be
    # telling us about the credentials the running process already loaded.
    try:
        status = exec(CMD_STATUS)
    except CrowdSecReloadError as exc:
        return CapiApplyResult(False, str(exc), True, True)
    if not parse_capi_status(status):
        tail = status.output.strip().splitlines()[-1:] or ["no output"]
        return CapiApplyResult(
            False,
            f"The new credentials were not accepted either: {tail[0]}",
            True,
            True,
        )
    return CapiApplyResult(True, None, True, True)


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
