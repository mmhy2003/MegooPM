# CrowdSec 1.8 Upgrade and WAF Upload Limit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run CrowdSec v1.8.1 and the streaming Lua bouncer, and let an admin set the WAF's request-body limit and over-limit action from a new Security → Settings tab, applied at once with a restart and a blocklist safety net.

**Architecture:** A MegooPM-owned AppSec config (`megoopm/body-size`, `on_load` hooks) is rendered from two new `instance_settings` columns, written in place to a single-file mount, and loaded after `crowdsecurity/appsec-default` via `appsec_configs`. A Celery task applies it like the whitelist apply (test → restart → health → rollback), with one extra rung: when CrowdSec cannot start, the blocklist is on and CrowdSec's firewall is blocking us, it switches the blocklist off and restarts again. A new admin tab reads one status endpoint and saves through one PATCH.

**Tech Stack:** FastAPI, SQLAlchemy 2 + Alembic (Postgres), Celery + Redis, pytest; Next.js + React, vitest + Testing Library; OpenResty + lua-cs-bouncer; CrowdSec v1.8.1 (Docker).

**Spec:** `docs/superpowers/specs/2026-10-25-crowdsec-upgrade-waf-body-limit-design.md`. Approved mockup: `.superpowers/brainstorm/1629-1792917029/content/settings-tab-v1.html` (local, untracked).

## Global Constraints

- CrowdSec image: `crowdsecurity/crowdsec:v1.8.1` in `docker-compose.yml`, `docker-compose.dev.yml`, `docker-compose.ha.yml` (and the e2e script `infra/nginx/tests/bouncer-phase.sh`).
- Lua bouncer: `CS_BOUNCER_VERSION=v1.0.19` in `infra/nginx/Dockerfile`.
- Defaults (migration column defaults **and** the seeded file): **10 MB (10485760 bytes), action `partial`**.
- Size range: whole MB, **1 to 2048** inclusive. Action: `drop` | `partial` | `allow`.
- AppSec config name `megoopm/body-size`; listed **last** in `appsec_configs`, after `crowdsecurity/appsec-default`.
- Data-path file: `/data/crowdsec/appsec/megoopm-body-size.yaml`; container path `/etc/crowdsec/appsec-configs/megoopm-body-size.yaml`, read-only.
- Files CrowdSec reads are written **in place**, never temp-then-rename (single-file bind mounts pin the inode).
- An unchanged render restarts nothing.
- Save always restarts; the safety net (switch the blocklist off) fires only after a failed restart, with the blocklist on, and `capi.probe_edge_block()` returning `True` at that moment.
- Copy for the three choices is fixed by the spec's table (*Decided in the brainstorm*); use it verbatim.
- Backend tests need Linux (`fcntl`): run them in a container (e.g. `python:3.12-slim` with `pip install ".[dev]" fakeredis`, backend mounted at `/app`). Do not start the dev stack's CrowdSec from the office connection CrowdSec is blocking: tasks 1, 2 and 9 need a connection it is not blocking.

## Review Focus

1. **A large upload through a protected host times out in the bouncer** (it now streams the body to AppSec; send/process timeouts are per operation) — expected: a 600 MB upload with `partial` or `allow` reaches the backend. Pinned in Task 9.
2. **An upgraded install whose data path has no `crowdsec/appsec/` yet** — expected: `data-init` creates and seeds the file before CrowdSec starts; Docker must never create a directory at the mount source. Pinned in Task 5 and Task 9.
3. **GB input and the bounds** (1.5 GB, 2 GB, 2049 MB, 0, blank) — expected: whole MB, 2048 accepted, 2049 and 0 refused with a field message. Pinned in Task 7 (schema) and Task 8 (UI helpers).
4. **A failed restart that is not caused by the block** — expected: the blocklist is left alone and the ordinary rollback runs. Pinned in Task 4.
5. **Saving while a CAPI toggle or another WAF apply runs** — expected: 409 from the PATCH, or `{"ran": False}` from a task that loses the lock; never two restarts racing. Pinned in Task 6 and Task 7.

---

### Task 1: CrowdSec v1.8.1 and MegooPM's `cscli` contract

Needs a connection CrowdSec is not blocking (hub downloads).

**Files:**
- Modify: `docker-compose.yml`, `docker-compose.dev.yml`, `docker-compose.ha.yml` (crowdsec `image:`)
- Modify: `infra/nginx/tests/bouncer-phase.sh` (its CrowdSec image)
- Modify, only if a format changed: `backend/app/services/crowdsec/hub.py` (`parse_hub_list`, `parse_agent_version`, `_AGENT_WARNING`, `HUB_ITEM_TYPES`), `backend/app/services/crowdsec/capi.py` (`parse_capi_status`), `backend/app/services/crowdsec/whitelists.py` (`config_test_error`)
- Test: `backend/tests/test_crowdsec_hub.py`, `backend/tests/test_crowdsec_capi.py`, `backend/tests/test_crowdsec_whitelists*.py`
- Modify: `docs/crowdsec.md` (every `v1.6.4` reference)

**Interfaces:** Produces nothing new; later tasks rely on the existing parsers working against v1.8.1.

- [ ] **Step 1: Bump the image** in the four files to `crowdsecurity/crowdsec:v1.8.1`.
- [ ] **Step 2: Capture real output** from a v1.8.1 container started with this repo's `COLLECTIONS`: `cscli version`, `cscli hub list -o json`, `cscli hub update` (note any release-notice line), `cscli capi status` with and without the blocklist on, `crowdsec -t` on a good config and on a config with a broken whitelist expression, and `ls /etc/crowdsec` (item directories). Confirm `crowdsecurity/appsec-default` is installed under that name.
- [ ] **Step 3: Add one test per parser fed the captured v1.8.1 text** (e.g. `test_parse_hub_list_reads_v1_8_output`, `test_parse_agent_version_reads_v1_8`, `test_parse_capi_status_accepts_v1_8_success`, `test_config_test_error_keeps_v1_8_fatal_line`), asserting the same results the v1.6.4 tests assert.
- [ ] **Step 4: Run them.** `python -m pytest tests/test_crowdsec_hub.py tests/test_crowdsec_capi.py tests/test_crowdsec_whitelists*.py -q`. Any failure is a changed format: fix the parser until the old and new tests both pass.
- [ ] **Step 5: Check the LAPI client** against a running v1.8.1 stack: the Security page's Dashboard, Active decisions, Recent alerts load; the backend machine auto-registers (`docker compose logs backend | grep -i crowdsec`).
- [ ] **Step 6: Update `docs/crowdsec.md`** version references; add an "Upgrading from 1.6.4" note: the container is recreated, so with the blocklist on it must start while CrowdSec's firewall is not blocking the address (or with the blocklist off).
- [ ] **Step 7: Commit** — `chore(crowdsec): run v1.8.1`.

### Task 2: Streaming Lua bouncer v1.0.19

Needs a connection CrowdSec is not blocking (the e2e script starts CrowdSec).

**Files:**
- Modify: `infra/nginx/Dockerfile` (`ARG CS_BOUNCER_VERSION=v1.0.19`)
- Modify, if needed: `infra/nginx/crowdsec-bouncer.conf`, `infra/nginx/lua/megoopm_crowdsec_init.lua`, `infra/nginx/lua/megoopm_crowdsec_check.lua`
- Test: `infra/nginx/tests/bouncer-phase.sh`

**Interfaces:** Our handlers call only `csmod.init(conf, ua)` and `csmod.Allow(ip)`; both exist in v1.0.19.

- [ ] **Step 1: Bump the ARG; diff `crowdsec-bouncer.conf` against v1.0.19's `config_example.conf`.** Keep our values; add nothing unless a key we rely on was renamed. Confirm in v1.0.19's `lib/crowdsec.lua` that AppSec still runs inside `Allow()` for every request when `APPSEC_URL` is set (the spec's "global" assumption); if not, stop and report.
- [ ] **Step 2: Build** `docker build infra/nginx` — passes (the Dockerfile's `test -f .../crowdsec.lua`).
- [ ] **Step 3: Run** `MSYS_NO_PATHCONV=1 bash infra/nginx/tests/bouncer-phase.sh` — exits 0.
- [ ] **Step 4: Commit** — `chore(nginx): lua-cs-bouncer v1.0.19, bodies streamed to AppSec`.

### Task 3: Settings columns, the action enum, the job kind, config

**Files:**
- Modify: `backend/app/models/enums.py` — add `class AppSecBodyAction(enum.StrEnum): drop, partial, allow`; add `waf_apply = "waf_apply"` to `CrowdSecJobKind`
- Modify: `backend/app/models/instance_settings.py` — `crowdsec_appsec_max_body_bytes: Mapped[int]` (BigInteger, not null, default/server_default `10485760`), `crowdsec_appsec_body_action: Mapped[AppSecBodyAction]` (enum type `appsec_body_action`, not null, default/server_default `partial`)
- Create: `backend/alembic/versions/0041_waf_body_limit.py` (revises `0040_capi_blocked`): create the enum type, add both columns, and `ALTER TYPE crowdsec_job_kind ADD VALUE IF NOT EXISTS 'waf_apply'` inside `op.get_context().autocommit_block()` (pattern: `0027_invitations.py`); downgrade drops the columns and type, leaves the enum value (one-way door, say so)
- Modify: `backend/app/core/config.py` — `crowdsec_waf_config_path: str = "/data/crowdsec/appsec/megoopm-body-size.yaml"`, `crowdsec_appsec_url: str = "http://crowdsec:7422"` (blank = AppSec not wired)
- Test: `backend/tests/test_waf_migration.py` (Postgres; mirror `test_capi_health_migration.py`)

**Interfaces — Produces:** `AppSecBodyAction`, `CrowdSecJobKind.waf_apply`, the two columns, `settings.crowdsec_waf_config_path`, `settings.crowdsec_appsec_url`.

- [ ] **Step 1: Write `test_upgrade_adds_the_defaults_and_the_job_kind`** — after `alembic upgrade head` on a row inserted at `0040`, the row reads `10485760` / `'partial'`, and inserting a `crowdsec_job_run` with kind `'waf_apply'` succeeds; `test_downgrade_drops_the_columns`.
- [ ] **Step 2: Run** (Postgres reachable) — fails, no revision `0041`.
- [ ] **Step 3: Implement the enum, columns, migration and settings.**
- [ ] **Step 4: Run** the new test and `tests/test_crowdsec_update_tasks.py` (its SQLite fixture inserts an `instance_settings` row) — pass.
- [ ] **Step 5: Commit** — `feat(crowdsec): store the WAF body limit`.

### Task 4: Render and apply the body-size config, with the safety net

**Files:**
- Create: `backend/app/services/crowdsec/waf.py`
- Test: `backend/tests/test_crowdsec_waf.py`

**Interfaces:**
- Consumes: `AppSecBodyAction` (Task 3); `capi.render_config_local(capi_enabled=False)`; `ExecResult`, `CrowdSecReloadError` from `reload.py`; `config_test_error` from `whitelists.py`.
- Produces:
  - `WAF_CONFIG_NAME = "megoopm/body-size"`, `DEFAULT_MAX_BODY_BYTES = 10 * 1024 * 1024`, `DEFAULT_ACTION = AppSecBodyAction.partial`
  - `render_body_size_config(*, max_body_bytes: int, action: AppSecBodyAction) -> str`
  - `@dataclass(frozen=True, slots=True) class WafApplyResult: ok: bool; error: str | None; restarted: bool; blocklist_switched_off: bool` with `as_dict() -> dict`
  - `run_waf_apply(*, content: str, path: Path, config_local_path: Path, capi_enabled: bool, validate: Callable[[], ExecResult], restart: Callable[[], None], healthy: Callable[[], bool], edge_blocked: Callable[[], bool | None]) -> WafApplyResult`

Rendered file (header comment like `capi._BASE`'s, then):

```yaml
name: megoopm/body-size
on_load:
  - apply:
      - SetMaxBodySize(<bytes>)
      - SetBodySizeExceededAction("<action>")
```

`run_waf_apply` order: unchanged content → `ok`, no restart · write in place → `validate()`; error or `CrowdSecReloadError` → restore, `ok=False`, no restart · `restart()` → `healthy()` → `ok` · else if `capi_enabled and edge_blocked() is True`: write `render_config_local(capi_enabled=False)` to `config_local_path`, restart, healthy → `ok=True, blocklist_switched_off=True` · otherwise (or still unhealthy): restore the previous body-size content and, if changed, the previous `config.yaml.local`, restart, `ok=False` with "CrowdSec did not come back … the previous limit was restored". A `CrowdSecReloadError` from any restart is reported in `error`, never raised.

- [ ] **Step 1: Write the tests** (fakes as in `test_crowdsec_capi_health.py`):
  - `test_render_has_the_bytes_and_the_action` — `SetMaxBodySize(104857600)`, `SetBodySizeExceededAction("allow")`, `name: megoopm/body-size`
  - `test_unchanged_content_restarts_nothing`
  - `test_a_config_crowdsec_refuses_is_restored_without_a_restart`
  - `test_a_healthy_restart_applies`
  - `test_blocked_and_down_switches_the_blocklist_off_and_keeps_the_new_limit` — config.local loses `online_client:`, body file has the new content, two restarts, `blocklist_switched_off is True`
  - `test_down_but_not_blocked_rolls_back_and_leaves_the_blocklist_alone` (Review Focus 4) — `edge_blocked` → `False`; config.local unchanged; body file restored
  - `test_down_with_the_blocklist_off_never_probes` — `edge_blocked` not called
  - `test_still_down_after_the_switch_off_restores_both_files`
  - `test_a_failing_restart_is_reported_not_raised`
- [ ] **Step 2: Run** `python -m pytest tests/test_crowdsec_waf.py -q` — fails, no module.
- [ ] **Step 3: Implement `waf.py`.** File I/O in place, as `capi._read` / `capi._write` do.
- [ ] **Step 4: Run** — pass.
- [ ] **Step 5: Commit** — `feat(crowdsec): apply the WAF body limit, with a blocklist safety net`.

### Task 5: Wire the file into CrowdSec, seed it, back it up

**Files:**
- Create: `infra/crowdsec/appsec-body-size.yaml` — byte-for-byte `render_body_size_config(max_body_bytes=10485760, action=partial)`
- Modify: `infra/crowdsec/acquis/appsec.yaml` — `appsec_configs: [crowdsecurity/appsec-default, megoopm/body-size]` (block list form), replacing `appsec_config:`; update its header comment
- Modify: all three compose files —
  - `data-init` command: `mkdir -p … /data/crowdsec/appsec` and `{ [ -f /data/crowdsec/appsec/megoopm-body-size.yaml ] || cp /seed/appsec-body-size.yaml /data/crowdsec/appsec/megoopm-body-size.yaml; }`; mount `./infra/crowdsec/appsec-body-size.yaml:/seed/appsec-body-size.yaml:ro`
  - `crowdsec` volumes: prod/dev `type: volume, source: app_data, target: /etc/crowdsec/appsec-configs/megoopm-body-size.yaml, read_only: true, volume.subpath: crowdsec/appsec/megoopm-body-size.yaml`; HA `${SHARED_DATA_PATH:?}/crowdsec/appsec/megoopm-body-size.yaml:/etc/crowdsec/appsec-configs/megoopm-body-size.yaml:ro`
  - backend env anchor: `CROWDSEC_APPSEC_URL: ${CROWDSEC_APPSEC_URL:-http://crowdsec:7422}` (same default nginx uses)
- Modify: `backend/app/tasks/backup.py` — `_crowdsec_files()` gains `"appsec-body-size.yaml": Path(settings.crowdsec_waf_config_path)`
- Test: `backend/tests/test_crowdsec_waf.py`, the backup tests that enumerate `_crowdsec_files`

- [ ] **Step 1: Write `test_the_seed_is_the_default_render`** — reads `infra/crowdsec/appsec-body-size.yaml` (path relative to the repo; skip with a reason if absent in the container) and asserts equality with the default render; and a backup test asserting the new key is in `_crowdsec_files()`.
- [ ] **Step 2: Run** — fails.
- [ ] **Step 3: Implement the seed, acquisition, compose and backup changes.**
- [ ] **Step 4: Run** the tests — pass. `docker compose -f docker-compose.yml config -q` (and dev, HA with a dummy `.env`) — valid.
- [ ] **Step 5: Upgrade check** (Review Focus 2) on a stack built from the previous commit: pull this change, `docker compose up -d` — `data-init` seeds the file, `/data/crowdsec/appsec/megoopm-body-size.yaml` is a regular file, CrowdSec starts and logs both appsec configs loading.
- [ ] **Step 6: Commit** — `feat(crowdsec): load MegooPM's body-size AppSec config`.

### Task 6: The apply task

**Files:**
- Modify: `backend/app/tasks/crowdsec.py`
- Modify: `backend/app/core/celery_app.py` — route `app.tasks.crowdsec.apply_waf_settings` to the control node queue with the other CrowdSec tasks
- Test: `backend/tests/test_crowdsec_update_tasks.py`

**Interfaces:**
- Consumes: `waf.run_waf_apply`, `waf.render_body_size_config`, `WafApplyResult` (Task 4); `start_job_run`/`finish_job_run`; `_run_locked`; `_edge_blocked`.
- Produces: `WAF_LOCK_KEY = "megoopm:crowdsec:waf-apply"`; Celery task `app.tasks.crowdsec.apply_waf_settings() -> dict` returning `WafApplyResult.as_dict()` or `{"ran": False, "reason": "already running"}`.

Behaviour: hold `WAF_LOCK_KEY` and then `CAPI_LOCK_KEY` (nested `_run_locked`; losing either returns `{"ran": False, …}`). Load the row; `start_job_run(kind=waf_apply, trigger=manual)`; call `run_waf_apply(capi_enabled=<what config.yaml.local says, via capi.read_capi_enabled>, …)`; `finish_job_run(… detail={"max_body_bytes": …, "action": …, "blocklist_switched_off": …})`. When `blocklist_switched_off`: set `crowdsec_capi_enabled = False` and write a finished `capi_apply` run, `ok=True`, `restarted=True`, `detail={"enabled": False, "reason": "blocked"}`.

- [ ] **Step 1: Write the tests** (existing `engine`/`fakes` fixtures; patch `tasks._edge_blocked`):
  - `test_apply_waf_writes_the_file_and_records_the_run`
  - `test_apply_waf_switch_off_turns_the_blocklist_off_everywhere` — settings row `crowdsec_capi_enabled is False`; `capi_apply` run detail `{"enabled": False, "reason": "blocked"}`
  - `test_apply_waf_skips_while_the_capi_lock_is_held` (Review Focus 5)
- [ ] **Step 2: Run** — fails.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `python -m pytest tests/test_crowdsec_update_tasks.py -q` — pass.
- [ ] **Step 5: Commit** — `feat(crowdsec): task that applies the WAF limit`.

### Task 7: API — read the WAF status, save the setting

**Files:**
- Modify: `backend/app/schemas/crowdsec.py`, `backend/app/schemas/instance_settings.py`
- Modify: `backend/app/api/routes/crowdsec.py`, `backend/app/api/routes/settings.py`, `backend/app/services/instance_settings.py`
- Modify: `backend/openapi.json` (`python -m scripts.export_openapi`)
- Test: `backend/tests/test_crowdsec_waf_api.py`

**Interfaces — Produces:**
- `CrowdSecWafUpdate(BaseModel)`: `max_body_mb: int = Field(ge=1, le=2048)`, `body_exceeded_action: AppSecBodyAction`
- `ProtectedHost(BaseModel)`: `kind: Literal["proxy", "redirection", "dead"]`, `id: int`, `name: str` (first domain)
- `CrowdSecWafStatus(BaseModel)`: `max_body_mb: int`, `body_exceeded_action: AppSecBodyAction`, `run: CrowdSecJobRunRead | None`, `running: bool`, `reload_configured: bool`, `appsec_configured: bool`, `protected_hosts: list[ProtectedHost]`, `capi_enabled: bool`, `capi_blocked: bool`
- `GET /api/v1/crowdsec/waf` → `CrowdSecWafStatus` (admin). `protected_hosts`: enabled hosts with `crowdsec_enabled`, all three kinds, sorted by kind then name. `appsec_configured = bool(settings.crowdsec_appsec_url.strip())`. `running`: `WAF_LOCK_KEY` held. `capi_blocked`: `crowdsec_capi_status_blocked`.
- `PATCH /api/v1/settings/crowdsec-waf` (`CrowdSecWafUpdate`) → 202 `InstanceSettingsRead`; stores bytes (`mb * 1024 * 1024`); audits `meta={"crowdsec_waf": …}`; 409 `"An apply is already running."` when `WAF_LOCK_KEY` or `CAPI_LOCK_KEY` is held (checked before storing); 409 `RELOADS_NOT_CONFIGURED` when the enqueue fails (after storing, as `crowdsec-capi` does). Service: `update_crowdsec_waf(db, *, max_body_bytes: int, action: AppSecBodyAction) -> InstanceSettings`.
- `InstanceSettingsRead` gains `crowdsec_appsec_max_body_mb: int`, `crowdsec_appsec_body_action: AppSecBodyAction`.

- [ ] **Step 1: Write the tests:**
  - `test_waf_status_defaults` — `max_body_mb == 10`, action `partial`, `run is None`
  - `test_protected_hosts_are_every_crowdsec_host` — one proxy with `crowdsec_enabled`, one without, one disabled redirection, one dead host → only the enabled crowdsec ones
  - `test_appsec_not_configured_when_the_url_is_blank`
  - `test_save_stores_bytes_and_queues_the_apply` — 202, `sent == [("app.tasks.crowdsec.apply_waf_settings", …)]`, row `== 2048 * 1024 * 1024` for `max_body_mb=2048`
  - `test_save_refuses_out_of_range` (Review Focus 3) — `0` and `2049` → 422
  - `test_save_is_409_while_an_apply_runs` (Review Focus 5) — CAPI lock held → 409, row unchanged
  - `test_waf_routes_are_admin_only`
- [ ] **Step 2: Run** — fails.
- [ ] **Step 3: Implement**, then regenerate `openapi.json`.
- [ ] **Step 4: Run** the new tests and `tests/test_openapi.py` — pass.
- [ ] **Step 5: Commit** — `feat(api): WAF upload limit status and setting`.

### Task 8: The Settings tab

**Files:**
- Modify: `frontend/src/lib/api/generated/schema.ts` (`npm run gen:api`), `frontend/src/lib/api/resources/crowdsec.ts` (`waf: () => api.get<CrowdSecWafStatus>(\`${BASE}/waf\`)`), `frontend/src/lib/api/resources/settings.ts` (`updateCrowdSecWaf: (body: CrowdSecWafUpdate) => api.patch<InstanceSettings>(\`${BASE}/crowdsec-waf\`, body)`), `frontend/src/lib/api/index.ts` (export the new types)
- Create: `frontend/src/components/security/waf-lib.ts`, `waf-lib.test.ts`
- Create: `frontend/src/components/security/waf-settings-card.tsx`, `waf-settings-card.test.tsx`
- Create: `frontend/src/components/security/settings-tab.tsx` (loads `crowdsec.waf()`, polls while `running`, like `UpdatesTab`)
- Modify: `frontend/src/components/security/security-view.tsx` — `settings` tab after `updates`, admin-only (`canWrite`), lucide `Settings2` icon
- Modify: `frontend/src/components/security/updates-lib.ts` (`describeCapiRun`) + its test

**Interfaces — Produces (`waf-lib.ts`):**
- `type SizeUnit = "MB" | "GB"`
- `toMb(value: string, unit: SizeUnit): number | null` — whole MB, `null` when not a number, ≤ 0, or > 2048
- `fromMb(mb: number): { value: string; unit: SizeUnit }` — GB when divisible by 1024
- `MEMORY_NOTE_MB = 100`
- `ACTION_COPY: Record<AppSecBodyAction, { title: string; tag: string; explain: (limit: string) => string; gain: string; cost: string }>` — the spec's table verbatim
- `describeWafRun(status: CrowdSecWafStatus): { label: string; failed: boolean }` — running → "Applying…"; no run → "Live: 10 MB · Check the first part — the default" (from the status values); failed run → `Failed: <error>`; a finished run whose `detail` values differ from the saved ones → "Saved — not applied yet"; otherwise "Applied <when> — CrowdSec restarted", plus " The community blocklist was switched off: CrowdSec's servers were blocking this address." when `detail.blocklist_switched_off`

Card layout follows the mockup: description ("…applies to every host protected by CrowdSec…"), host list (or "No hosts use CrowdSec yet"), size + unit, radio group of the three choices, memory note when `toMb(...) > MEMORY_NOTE_MB` ("CrowdSec holds up to this much of each request body in memory while it checks it…"), "AppSec is off — the limit is saved but has no effect" when `!appsec_configured`, status line, **Save and restart CrowdSec** → `ConfirmDeleteDialog` (title "Restart CrowdSec to apply?"; when `capi_enabled && capi_blocked` the description adds that CrowdSec's servers are blocking this address and the restart may switch the community blocklist off to bring CrowdSec back).

`describeCapiRun`: a finished `ok` run with `detail.reason === "blocked"` and `enabled === false` → `"Off — switched off because CrowdSec's servers were blocking this address. Turn it back on once the block lifts."`

- [ ] **Step 1: Write the tests:**
  - `waf-lib.test.ts`: `toMb("1.5","GB") === 1536`, `toMb("2","GB") === 2048`, `toMb("2049","MB") === null`, `toMb("0","MB") === null`, `toMb("","MB") === null`, `toMb("12.5","MB") === null`; `fromMb(2048)` → `{value:"2",unit:"GB"}`, `fromMb(100)` → `{value:"100",unit:"MB"}`
  - `waf-settings-card.test.tsx`: lists protected hosts; the three choices show the spec's titles; memory note appears at 200 MB, not at 50 MB; Save disabled for `2049 MB`; Save opens the dialog and confirming calls `updateCrowdSecWaf({max_body_mb: 100, body_exceeded_action: "partial"})`; dialog warns about the blocklist only when `capi_enabled && capi_blocked`; "AppSec is off" when `appsec_configured` is false
  - `waf-lib.test.ts` (`describeWafRun`): no run → contains "the default"; a run applying 10 MB/partial while the status says 100 MB → "Saved — not applied yet"; `blocklist_switched_off` → mentions the blocklist
  - `updates-lib.test.ts`: the blocked switch-off label
- [ ] **Step 2: Run** `npx vitest run src/components/security` — the new tests fail.
- [ ] **Step 3: Implement.** Match surrounding formatting by hand (no project prettier config: do not run prettier).
- [ ] **Step 4: Run** `npx vitest run src/components/security`, `npm run typecheck`, `npx eslint src/components/security` — pass/clean.
- [ ] **Step 5: Commit** — `feat(security): Settings tab with the WAF upload limit`.

### Task 9: End to end, and the docs

Needs a connection CrowdSec is not blocking.

**Files:**
- Modify: `docs/crowdsec.md` — new section "WAF upload limit": the file, `appsec_configs` order, defaults and why, the three actions, the restart and the safety net, how to verify; AppSec-scope section mentions the limit is engine-wide
- Modify: `HA-SETUP.md` — the upgrade note: the control-plane node recreates CrowdSec; see the blocklist caveat

- [ ] **Step 1: Bring up the dev stack** on this branch; Security → Settings shows 10 MB / Check the first part and the protected hosts.
- [ ] **Step 2: Upload through a CrowdSec-enabled proxy host** to a backend that accepts any body (e.g. `docker run --rm -p 8099:80 kennethreitz/httpbin` or nginx with `client_max_body_size 0` returning 200):
  - `drop`, 10 MB: a 50 MB `curl -T` gets the ban page (403); a 5 MB one gets 200
  - `partial`, 10 MB: 50 MB → 200; a 50 MB body with `<script>` in its first KB → blocked; with it after 20 MB → 200
  - `allow`, 10 MB: 50 MB → 200
  - `partial`, 100 MB: **600 MB → 200** (Review Focus 1); nginx memory stays flat (`docker stats`)
- [ ] **Step 3: Save an unchanged value** — no restart (CrowdSec uptime unchanged).
- [ ] **Step 4: Write the docs.**
- [ ] **Step 5: Commit** — `docs(crowdsec): WAF upload limit and the 1.8 upgrade`.
