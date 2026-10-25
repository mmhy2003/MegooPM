"""The pure parts of the hub refresh, and the flow with fakes."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.models.enums import HubUpdateFrequency
from app.services.crowdsec import hub
from app.services.crowdsec.reload import CrowdSecReloadError, ExecResult

LIST_BEFORE = json.dumps(
    {
        "collections": [
            {"name": "crowdsecurity/nginx", "local_version": "0.2", "status": "enabled"}
        ],
        "parsers": [
            {"name": "crowdsecurity/nginx-logs", "local_version": "0.5", "status": "enabled"}
        ],
        "scenarios": [],
        "appsec-rules": [],
    }
)
LIST_AFTER = json.dumps(
    {
        "collections": [
            {"name": "crowdsecurity/nginx", "local_version": "0.3", "status": "enabled"}
        ],
        "parsers": [
            {"name": "crowdsecurity/nginx-logs", "local_version": "0.5", "status": "enabled"},
            {"name": "crowdsecurity/new-thing", "local_version": "0.1", "status": "enabled"},
        ],
        "scenarios": [],
        "appsec-rules": [],
    }
)


# --- parsing ------------------------------------------------------------------


def test_parse_hub_list_keys_by_type_and_name() -> None:
    assert hub.parse_hub_list(LIST_BEFORE) == {
        "collections:crowdsecurity/nginx": "0.2",
        "parsers:crowdsecurity/nginx-logs": "0.5",
    }


def test_parse_hub_list_tolerates_garbage() -> None:
    assert hub.parse_hub_list("level=fatal not json") == {}


def test_diff_versions_reports_changed_and_new_never_removed() -> None:
    before, after = hub.parse_hub_list(LIST_BEFORE), hub.parse_hub_list(LIST_AFTER)
    assert hub.diff_versions(before, after) == [
        "collections:crowdsecurity/nginx",
        "parsers:crowdsecurity/new-thing",
    ]
    assert hub.diff_versions(after, before) == ["collections:crowdsecurity/nginx"]


def test_parse_agent_warning() -> None:
    text = (
        'level=warning msg="A new CrowdSec release is available (v1.8.0). '
        "Your version is 'v1.6.4'.\""
    )
    assert hub.parse_agent_warning(text) == "v1.8.0"
    assert hub.parse_agent_warning('level=info msg="Wrote index"') is None


def test_parse_agent_version() -> None:
    assert hub.parse_agent_version("version: v1.6.4-523164f6\nCodename: alphaga\n") == "v1.6.4"
    assert hub.parse_agent_version("") is None


# Captured from crowdsecurity/crowdsec:v1.8.1. `cscli version` capitalises its
# keys, and `cscli hub list -o json` writes a summary line to stderr first —
# which the exec's TTY merges into the same stream as the JSON.
V18_VERSION = "Version: v1.8.1-909b5157\nCodename: alphaga\nBuildDate: 2026-09-03_11:03:45\n"
V18_HUB_LIST = (
    "Loaded: 166 parsers, 12 postoverflows, 786 scenarios, 10 contexts, 18 appsec-configs, "
    "222 appsec-rules, 171 collections\n"
    "{\n"
    ' "appsec-configs": [],\n'
    ' "collections": [\n'
    "  {\n"
    '   "name": "crowdsecurity/linux",\n'
    '   "local_version": "0.4",\n'
    '   "local_path": "/etc/crowdsec/collections/linux.yaml",\n'
    '   "utf8_status": "✔️  enabled",\n'
    '   "status": "enabled"\n'
    "  }\n"
    " ]\n"
    "}\n"
)


def test_parse_agent_version_reads_v1_8() -> None:
    assert hub.parse_agent_version(V18_VERSION) == "v1.8.1"


def test_parse_hub_list_reads_v1_8_output() -> None:
    assert hub.parse_hub_list(V18_HUB_LIST) == {"collections:crowdsecurity/linux": "0.4"}


def test_parse_agent_warning_reads_v1_8() -> None:
    # Same wording in v1.8.1 (cmd/crowdsec-cli/core/require/branch.go).
    text = "WARN A new CrowdSec release is available (v1.9.0). Your version is 'v1.8.1'."
    assert hub.parse_agent_warning(text) == "v1.9.0"


def test_output_tail_keeps_the_last_lines() -> None:
    text = "\n".join(f"line {i}" for i in range(50))
    tail = hub.output_tail(text, lines=3)
    assert tail == "line 47\nline 48\nline 49"


# --- is it due? -----------------------------------------------------------------


def _at(y: int, mo: int, d: int, h: int) -> datetime:
    return datetime(y, mo, d, h, 5, tzinfo=UTC)


def test_daily_is_due_at_the_hour_and_not_otherwise() -> None:
    kw = {
        "auto_update": True,
        "frequency": HubUpdateFrequency.daily,
        "weekday": 6,
        "hour_utc": 3,
        "last_started_at": None,
    }
    assert hub.is_due(now=_at(2026, 9, 4, 3), **kw)[0] is True
    assert hub.is_due(now=_at(2026, 9, 4, 4), **kw)[0] is False


def test_weekly_needs_the_weekday_too() -> None:
    # 2026-09-06 is a Sunday (weekday 6); 2026-09-04 is a Friday.
    kw = {
        "auto_update": True,
        "frequency": HubUpdateFrequency.weekly,
        "weekday": 6,
        "hour_utc": 3,
        "last_started_at": None,
    }
    assert hub.is_due(now=_at(2026, 9, 6, 3), **kw)[0] is True
    assert hub.is_due(now=_at(2026, 9, 4, 3), **kw)[0] is False


def test_not_due_when_off_or_already_ran_this_hour() -> None:
    off = hub.is_due(
        now=_at(2026, 9, 4, 3),
        auto_update=False,
        frequency=HubUpdateFrequency.daily,
        weekday=6,
        hour_utc=3,
        last_started_at=None,
    )
    assert off == (False, "auto-update is off")
    ran = hub.is_due(
        now=_at(2026, 9, 4, 3),
        auto_update=True,
        frequency=HubUpdateFrequency.daily,
        weekday=6,
        hour_utc=3,
        last_started_at=datetime(2026, 9, 4, 3, 1, tzinfo=UTC),
    )
    assert ran == (False, "already ran this hour")
    yesterday = hub.is_due(
        now=_at(2026, 9, 4, 3),
        auto_update=True,
        frequency=HubUpdateFrequency.daily,
        weekday=6,
        hour_utc=3,
        last_started_at=datetime(2026, 9, 3, 3, 1, tzinfo=UTC),
    )
    assert yesterday[0] is True


def test_a_naive_last_run_is_treated_as_utc() -> None:
    # SQLite hands back naive datetimes; the check must not raise on them.
    ran = hub.is_due(
        now=_at(2026, 9, 4, 3),
        auto_update=True,
        frequency=HubUpdateFrequency.daily,
        weekday=6,
        hour_utc=3,
        last_started_at=datetime(2026, 9, 4, 3, 1),
    )
    assert ran == (False, "already ran this hour")


# --- the flow, with fakes ---------------------------------------------------------


class FakeContainer:
    """Answers each command from a script; records what ran."""

    def __init__(
        self,
        *,
        lists: list[str],
        upgrade: ExecResult | None = None,
        update: ExecResult | None = None,
    ):
        self.lists = list(lists)
        self.upgrade = upgrade or ExecResult(0, "updated crowdsecurity/nginx\n")
        self.update = update or ExecResult(0, 'level=info msg="Wrote index"\n')
        self.ran: list[list[str]] = []
        self.restarts = 0

    def exec(self, argv: list[str]) -> ExecResult:
        self.ran.append(argv)
        if argv == hub.CMD_LIST:
            return ExecResult(0, self.lists.pop(0))
        if argv == hub.CMD_VERSION:
            return ExecResult(0, "version: v1.6.4-abc\n")
        if argv == hub.CMD_UPDATE:
            return self.update
        if argv == hub.CMD_UPGRADE:
            return self.upgrade
        if argv in (hub.CMD_BACKUP, hub.CMD_RESTORE):
            return ExecResult(0, "")
        raise AssertionError(f"unexpected command {argv}")

    def restart(self) -> None:
        self.restarts += 1


def test_nothing_changed_means_no_restart() -> None:
    c = FakeContainer(lists=[LIST_BEFORE, LIST_BEFORE], upgrade=ExecResult(0, ""))
    result = hub.run_hub_update(exec=c.exec, restart=c.restart, healthy=lambda: True)
    assert result.ok and not result.restarted and result.updated == []
    assert c.restarts == 0
    assert hub.CMD_BACKUP in c.ran and hub.CMD_UPDATE in c.ran and hub.CMD_UPGRADE in c.ran
    assert result.agent_version == "v1.6.4"


def test_a_change_restarts_and_records_it() -> None:
    c = FakeContainer(
        lists=[LIST_BEFORE, LIST_AFTER],
        update=ExecResult(
            0,
            'level=warning msg="A new CrowdSec release is available (v1.8.0). '
            "Your version is 'v1.6.4'.\"\n",
        ),
    )
    result = hub.run_hub_update(exec=c.exec, restart=c.restart, healthy=lambda: True)
    assert result.ok and result.restarted
    assert result.updated == ["collections:crowdsecurity/nginx", "parsers:crowdsecurity/new-thing"]
    assert result.latest_agent_version == "v1.8.0"
    assert c.restarts == 1
    assert hub.CMD_RESTORE not in c.ran


def test_unhealthy_after_restart_restores_the_backup_and_restarts_again() -> None:
    c = FakeContainer(lists=[LIST_BEFORE, LIST_AFTER])
    result = hub.run_hub_update(exec=c.exec, restart=c.restart, healthy=lambda: False)
    assert not result.ok and result.restarted
    assert "did not come back" in (result.error or "")
    assert c.ran[-1] == hub.CMD_RESTORE
    assert c.restarts == 2


def test_a_failed_upgrade_stops_before_any_restart() -> None:
    c = FakeContainer(
        lists=[LIST_BEFORE], upgrade=ExecResult(1, 'a\nb\nlevel=fatal msg="network down"\n')
    )
    result = hub.run_hub_update(exec=c.exec, restart=c.restart, healthy=lambda: True)
    assert not result.ok and not result.restarted
    assert "network down" in (result.error or "")
    assert c.restarts == 0


# What `cscli hub update` printed on a blocked office connection, 2026-10-25
# (colours already stripped by the exec).
BLOCKED_UPDATE = (
    "WARN A new CrowdSec release is available (v1.8.0). Your version is 'v1.6.4'. "
    "Please update it to use new parsers/scenarios/collections.\n"
    "WARN Failed to check last modified: bad HTTP code 403 for "
    "https://cdn-hub.crowdsec.net/crowdsecurity/v1.6.4/.index.json "
    'url="https://cdn-hub.crowdsec.net/crowdsecurity/v1.6.4/.index.json"\n'
    "FATA failed to update hub: bad HTTP code 403 for "
    "https://cdn-hub.crowdsec.net/crowdsecurity/v1.6.4/.index.json\n"
)


def test_a_refused_hub_download_says_why_in_plain_words() -> None:
    c = FakeContainer(lists=[LIST_BEFORE], update=ExecResult(1, BLOCKED_UPDATE))
    result = hub.run_hub_update(exec=c.exec, restart=c.restart, healthy=lambda: True)

    assert not result.ok and not result.restarted
    error = result.error or ""
    assert "internet address" in error
    # The engine's own last word stays, so the cause is not guesswork.
    assert error.endswith(
        "failed to update hub: bad HTTP code 403 for "
        "https://cdn-hub.crowdsec.net/crowdsecurity/v1.6.4/.index.json"
    )
    # The card already shows the release; repeating it here buries the error.
    assert "new CrowdSec release" not in error
    # Still parsed for the card, though.
    assert result.latest_agent_version == "v1.8.0"


def test_a_refused_item_download_during_the_upgrade_says_why_too() -> None:
    c = FakeContainer(
        lists=[LIST_BEFORE],
        upgrade=ExecResult(
            1,
            "FATA failed to download item: bad HTTP code 403 for "
            "https://cdn-hub.crowdsec.net/crowdsecurity/v1.6.4/parsers/x.yaml\n",
        ),
    )
    result = hub.run_hub_update(exec=c.exec, restart=c.restart, healthy=lambda: True)
    assert not result.ok
    assert "internet address" in (result.error or "")


def test_other_failures_drop_the_release_notice_but_keep_the_rest() -> None:
    c = FakeContainer(
        lists=[LIST_BEFORE],
        update=ExecResult(
            1,
            "WARN A new CrowdSec release is available (v1.8.0). Your version is 'v1.6.4'.\n"
            "FATA failed to update hub: dial tcp: lookup cdn-hub.crowdsec.net: no such host\n",
        ),
    )
    result = hub.run_hub_update(exec=c.exec, restart=c.restart, healthy=lambda: True)
    assert result.error == (
        "hub update failed: FATA failed to update hub: dial tcp: lookup "
        "cdn-hub.crowdsec.net: no such host"
    )


def test_a_failed_restart_is_reported() -> None:
    c = FakeContainer(lists=[LIST_BEFORE, LIST_AFTER])

    def boom() -> None:
        raise CrowdSecReloadError("docker says no")

    result = hub.run_hub_update(exec=c.exec, restart=boom, healthy=lambda: True)
    assert not result.ok and "docker says no" in (result.error or "")


def test_exec_failure_is_reported_not_raised() -> None:
    def broken(argv: list[str]) -> ExecResult:
        raise CrowdSecReloadError("Could not reach the docker daemon")

    result = hub.run_hub_update(exec=broken, restart=lambda: None, healthy=lambda: True)
    assert not result.ok and "docker daemon" in (result.error or "")
