"""Refreshing CrowdSec's hub items: parsing, the due-check, and the flow.

Everything here is pure or takes its side effects as callables, so the
sequence — back up, update, upgrade, diff, restart only if something
changed, roll back if CrowdSec does not come back — is tested without a
docker socket. The Celery task in ``app.tasks.crowdsec`` supplies the real
``exec``/``restart``/``healthy``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from app.models.enums import HubUpdateFrequency
from app.services.crowdsec.reload import CrowdSecReloadError, ExecResult

HUB_ITEM_TYPES = (
    "hub",
    "collections",
    "parsers",
    "scenarios",
    "postoverflows",
    "contexts",
    "appsec-configs",
    "appsec-rules",
)
#: In the data volume, so it survives a restart and never lands in /etc/crowdsec.
HUB_BACKUP_PATH = "/var/lib/crowdsec/data/megoopm-hub-backup.tgz"

CMD_LIST = ["cscli", "hub", "list", "-o", "json"]
CMD_UPDATE = ["cscli", "hub", "update"]
CMD_UPGRADE = ["cscli", "hub", "upgrade"]
CMD_VERSION = ["cscli", "version"]
# Item directories are symlinks into hub/, so this captures the installed
# state. `ls -d` drops directories that do not exist on this install.
CMD_BACKUP = [
    "sh",
    "-c",
    f"cd /etc/crowdsec && tar -czf {HUB_BACKUP_PATH} "
    f"$(ls -d {' '.join(HUB_ITEM_TYPES)} 2>/dev/null)",
]
# Untar OVER: the whitelist file is a bind mount inside parsers/s02-enrich
# and cannot be removed, so nothing is deleted first.
CMD_RESTORE = ["tar", "-xzf", HUB_BACKUP_PATH, "-C", "/etc/crowdsec"]

_AGENT_WARNING = re.compile(r"new CrowdSec release is available \((v[\d.]+)\)")
_AGENT_VERSION = re.compile(r"^version:\s*(v[\d.]+)", re.MULTILINE)


# --- parsing ------------------------------------------------------------------


def parse_hub_list(text: str) -> dict[str, str]:
    """``{"<type>:<name>": local_version}`` for every installed item."""
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for item_type, items in data.items():
        for item in items or []:
            name = item.get("name") if isinstance(item, dict) else None
            if name:
                out[f"{item_type}:{name}"] = str(item.get("local_version") or "")
    return out


def diff_versions(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Items whose version changed or that are new. Removals are not changes."""
    return sorted(key for key, version in after.items() if before.get(key) != version)


def parse_agent_warning(text: str) -> str | None:
    match = _AGENT_WARNING.search(text)
    return match.group(1) if match else None


def parse_agent_version(text: str) -> str | None:
    match = _AGENT_VERSION.search(text)
    return match.group(1) if match else None


def output_tail(text: str, lines: int = 20) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


# --- the schedule ----------------------------------------------------------------


def is_due(
    *,
    now: datetime,
    auto_update: bool,
    frequency: HubUpdateFrequency,
    weekday: int,
    hour_utc: int,
    last_started_at: datetime | None,
) -> tuple[bool, str]:
    """Whether the hourly tick at ``now`` should run the job, and why not."""
    if not auto_update:
        return False, "auto-update is off"
    if now.hour != hour_utc:
        return False, "not the configured hour"
    if frequency is HubUpdateFrequency.weekly and now.weekday() != weekday:
        return False, "not the configured weekday"
    if last_started_at is not None:
        # SQLite (the test factory) hands back naive datetimes; treat as UTC.
        if last_started_at.tzinfo is None:
            last_started_at = last_started_at.replace(tzinfo=UTC)
        same_hour = last_started_at.replace(minute=0, second=0, microsecond=0) == now.replace(
            minute=0, second=0, microsecond=0
        )
        if same_hour:
            return False, "already ran this hour"
    return True, "due"


# --- the flow -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HubUpdateResult:
    ok: bool
    error: str | None
    restarted: bool
    updated: list[str]
    agent_version: str | None
    latest_agent_version: str | None

    def as_dict(self) -> dict:
        return asdict(self)


def run_hub_update(
    *,
    exec: Callable[[list[str]], ExecResult],
    restart: Callable[[], None],
    healthy: Callable[[], bool],
) -> HubUpdateResult:
    """Back up, update, upgrade, diff; restart only if something changed."""
    agent_version = None
    latest = None
    try:
        agent_version = parse_agent_version(exec(CMD_VERSION).output)
        before = parse_hub_list(exec(CMD_LIST).output)
        backup = exec(CMD_BACKUP)
        if backup.exit_code != 0:
            return HubUpdateResult(
                False,
                f"Backup failed: {output_tail(backup.output)}",
                False,
                [],
                agent_version,
                None,
            )
        update = exec(CMD_UPDATE)
        latest = parse_agent_warning(update.output)
        if update.exit_code != 0:
            return HubUpdateResult(
                False,
                f"hub update failed: {output_tail(update.output)}",
                False,
                [],
                agent_version,
                latest,
            )
        upgrade = exec(CMD_UPGRADE)
        if upgrade.exit_code != 0:
            return HubUpdateResult(
                False,
                f"hub upgrade failed: {output_tail(upgrade.output)}",
                False,
                [],
                agent_version,
                latest,
            )
        after = parse_hub_list(exec(CMD_LIST).output)
    except CrowdSecReloadError as exc:
        return HubUpdateResult(False, str(exc), False, [], agent_version, latest)

    updated = diff_versions(before, after)
    if not updated:
        return HubUpdateResult(True, None, False, [], agent_version, latest)

    try:
        restart()
    except CrowdSecReloadError as exc:
        return HubUpdateResult(False, str(exc), False, updated, agent_version, latest)
    if healthy():
        return HubUpdateResult(True, None, True, updated, agent_version, latest)

    # CrowdSec did not answer again: an upgraded item it cannot load. Put the
    # previous item files back and restart onto those.
    try:
        exec(CMD_RESTORE)
        restart()
    except CrowdSecReloadError as exc:
        return HubUpdateResult(
            False,
            "CrowdSec did not come back after the hub upgrade, and the rollback also "
            f"failed: {exc}",
            True,
            updated,
            agent_version,
            latest,
        )
    return HubUpdateResult(
        False,
        "CrowdSec did not come back after the hub upgrade. The previous rules were restored.",
        True,
        updated,
        agent_version,
        latest,
    )


__all__ = [
    "CMD_BACKUP",
    "CMD_LIST",
    "CMD_RESTORE",
    "CMD_UPDATE",
    "CMD_UPGRADE",
    "CMD_VERSION",
    "HUB_BACKUP_PATH",
    "HUB_ITEM_TYPES",
    "HubUpdateResult",
    "diff_versions",
    "is_due",
    "output_tail",
    "parse_agent_version",
    "parse_agent_warning",
    "parse_hub_list",
    "run_hub_update",
]
