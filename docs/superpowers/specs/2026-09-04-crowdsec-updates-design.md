# CrowdSec hub updates and the community blocklist — design

## Where this sits

CrowdSec's detection rules come from its hub: parsers, scenarios, collections
and AppSec rules. The official image refreshes them only when the container
starts. The community blocklist — CrowdSec's shared threat intelligence — is
switched off in both compose files. This project gives an admin a scheduled
hub refresh, an Update now button, and a switch for the blocklist, in a new
**Updates** tab on the Security page.

It reuses the whitelist machinery from MEG-36: the docker-socket restart, the
LAPI health wait, the write-restart-verify-rollback shape, and the HA routing
to the control-plane node.

## Decisions taken with the user

- **Scope of the tab:** schedule, Update now, and last-run status. Installed
  collections stay defined in compose.
- **The community blocklist gets an on/off switch** in the same tab.

## What was measured

Against `crowdsecurity/crowdsec:v1.6.4` (the pinned image), in throwaway
containers on 2026-09-03:

- **The entrypoint** runs `cscli hub update` at start only if the hub index
  is older than 24 hours, then `cscli hub upgrade`, both only when the config
  and data directories are volumes (they are). Nothing runs again while the
  container lives.
- **`DISABLE_ONLINE_API=true`** makes the entrypoint delete
  `api.server.online_client` from `config.yaml` on every start. With it
  unset, the entrypoint registers with CAPI at first boot by itself.
- **`config.yaml.local` is merged over `config.yaml` at load time**, after
  the entrypoint's edit. An `online_client` block there re-enables CAPI with
  the env untouched. `cscli capi register` refuses to run until that block
  exists ("no configuration for Central API"), so the override is written
  first, then `cscli capi register -f /etc/crowdsec/online_api_credentials.yaml`,
  then a restart. After that `cscli capi status` prints "You can successfully
  interact with Central API (CAPI)" and exits 0, and the log shows the
  community blocklist arriving ("added 15000 entries") with a pull interval of
  two hours.
- **An `online_client` block whose credentials file is missing stops CrowdSec
  from starting.** LAPI never comes back. Removing the block and restarting
  recovers it.
- **`cscli capi status` has no JSON output**, and exits non-zero with "no
  configuration for Central API" when the block is absent.
- **`cscli hub list -o json`** returns a dict keyed by item type
  (`collections`, `parsers`, `scenarios`, `appsec-rules`, …); each item has
  `name`, `local_version`, `local_path`, `status`.
- **`cscli hub update`** writes the index and warns when a newer agent exists
  ("A new CrowdSec release is available (v1.8.0). Your version is 'v1.6.4'").
- **`cscli hub upgrade`** has no dry-run; it prints `updated <item>` per change
  on stdout, `Upgraded N collections` on stderr, and exits 0 either way.
  `--force` re-downloads everything.
- The log warns that **SQLite without WAL may make LAPI unresponsive** while
  the blocklist is inserted. The entrypoint honours `USE_WAL=true`.

## What the code already does

- `app/services/crowdsec/reload.py` — `restart_container(name, socket_path=,
  timeout_seconds=, transport=)` over the docker socket with httpx, API
  v1.43, typed `CrowdSecReloadError`.
- `app/tasks/crowdsec.py` — `apply_whitelists_to_disk(docs, path=,
  applied_digest=, restart=, healthy=)`: render, digest short-circuit, write,
  restart, health, rollback. `_wait_for_lapi()` polls LAPI up to
  `crowdsec_reload_health_timeout_seconds`. The task is enqueued on demand
  from the whitelist routes, routed with `node_queue(crowdsec_control_node_id)`
  under HA and refused with 409 when `_reload_configured()` is false.
- `CrowdSecWhitelistApply` — a single-row status table: `applied_digest`,
  `applied_at`, `ok`, `error`. The Security page shows it in a banner with a
  retry.
- `instance_settings` — one row; every settings card has its own `PATCH
  /settings/<area>` with a Pydantic update model, and `InstanceSettingsRead`
  carries everything.
- `celery_app.beat_schedule` — static entries; HA routes per-node tasks to
  `megoopm.node.<id>` queues and every node runs beat.
- The Security page is four tabs (`dashboard`, `decisions`, `alerts`,
  `whitelists`) built on `Tabs`/`TabsList`/`TabsTab`/`TabsPanel`.
- Compose: HA mounts the docker socket and `${SHARED_DATA_PATH}` on the
  worker and bind-mounts the whitelist FILE into CrowdSec, seeded by
  `data-init` because a missing bind source becomes a directory. Dev mounts
  neither the socket nor a crowdsec data path on the worker, so reloads
  cannot work in dev today. Both files mount
  `./infra/crowdsec/config.yaml.local` read-only from the repo.

## Storage

Five columns on `instance_settings`:

| column | type | default |
| --- | --- | --- |
| `crowdsec_hub_auto_update` | bool | `true` |
| `crowdsec_hub_update_frequency` | enum `daily`, `weekly` | `daily` |
| `crowdsec_hub_update_weekday` | int 0–6, Monday = 0 | `6` (Sunday) |
| `crowdsec_hub_update_hour_utc` | int 0–23 | `3` |
| `crowdsec_capi_enabled` | bool | `false` |

The hour is stored in UTC. The UI shows and picks it in the browser's local
time and converts; a DST change shifts the slot by an hour, which is
accepted.

One new table, `crowdsec_job_run`, one row per kind:

| column | note |
| --- | --- |
| `kind` (PK) | enum `hub_update`, `capi_apply` |
| `started_at`, `finished_at` | timestamptz; `finished_at` null while running |
| `ok` | bool |
| `error` | text, null when ok |
| `trigger` | enum `scheduled`, `manual` |
| `restarted` | bool — CrowdSec was restarted by this run |
| `detail` | JSON: for `hub_update` `{updated: [names], agent_version, latest_agent_version}`; for `capi_apply` `{enabled: bool}` |

Migration `0030_crowdsec_updates`. The `crowdsec_capi_enabled` column is the
**desired** state; the `capi_apply` row is what was **achieved**. The UI shows
both when they differ.

## Running commands in the container

`app/services/crowdsec/reload.py` gains

```
exec_in_container(name, argv, *, socket_path, timeout_seconds, transport=None) -> ExecResult(exit_code: int, output: str)
```

over the same socket and API version: `POST /containers/{name}/exec` with
`AttachStdout`, `AttachStderr`, `Tty: true` (so the stream is plain text, no
multiplexing frames), `POST /exec/{id}/start`, then `GET /exec/{id}/json` for
`ExitCode`. Errors raise `CrowdSecReloadError` with the container and socket
named, as the restart does. The socket stays on the worker only.

## The hub job

`app/services/crowdsec/hub.py` holds the pure parts; `app/tasks/crowdsec.py`
gains the task.

Steps, with `exec`, `restart` and `healthy` as callables:

1. `cscli hub list -o json` → the *before* map `{name: local_version}` across
   all item types; also `cscli version` for the agent version.
2. Back up: `tar -czf /var/lib/crowdsec/data/megoopm-hub-backup.tgz -C
   /etc/crowdsec hub collections parsers scenarios postoverflows contexts
   appsec-configs appsec-rules`. Item directories are symlinks into `hub/`,
   so this captures the whole installed state. Missing directories are
   tolerated.
3. `cscli hub update`. Parse the "new CrowdSec release is available (vX)"
   warning into `latest_agent_version`.
4. `cscli hub upgrade`. A non-zero exit records the output tail as the error
   and stops; nothing was restarted.
5. `cscli hub list -o json` → the *after* map. `updated` = names whose
   `local_version` changed or that are new.
6. **Nothing changed → no restart**, `ok=true`, `restarted=false`. Same rule
   as whitelists: an idle run must not cost a fail-closed window.
7. Something changed → `restart()`, then `healthy()`. Healthy → `ok=true`,
   `restarted=true`.
8. Unhealthy → **rollback**: `tar -xzf … -C /etc/crowdsec` over the item
   directories (the whitelist file is a bind mount inside `parsers/s02-enrich`
   and cannot be removed, so this untars *over* rather than deleting), then
   `restart()` again. Record `ok=false` with the reason. Files the upgrade
   added and nothing references stay behind; that is harmless.

Every `cscli` step has a 120-second timeout; the two hub commands talk to
the internet.

## Scheduling

- A beat entry fires `app.tasks.crowdsec.hub_update_tick` every hour at
  minute 5, with `expires` under an hour so a backlog never runs twice.
- The tick reads the settings and the `hub_update` row. It runs the job when
  all of: auto-update is on; the current UTC hour equals the configured
  hour; for weekly, the current UTC weekday equals the configured weekday;
  and the last run did not start inside the current hour. Otherwise it
  returns `{"ran": false, "reason": …}`.
- The job itself, `app.tasks.crowdsec.update_hub(trigger)`, takes a Redis
  lock `megoopm:crowdsec:hub-update` for its duration. A second caller
  finds the lock and records nothing; Update now returns 409 to the user.
- Under HA both tasks are routed to `node_queue(crowdsec_control_node_id)`
  via `task_routes`; the tick from every node's beat lands on the control
  node, and the "already ran this hour" check makes the extra ticks no-ops.
  When `crowdsec_control_node_id` is unset in HA, the tick logs once and
  does nothing, and Update now is 409 with the whitelist wording.

## The community blocklist switch

The app takes ownership of `config.yaml.local`:

- It becomes a file under the CrowdSec data path —
  `crowdsec_config_local_path`, default `/data/crowdsec/config.yaml.local` —
  seeded by `data-init` from `infra/crowdsec/config.yaml.local` when absent,
  and bind-mounted read-only into the container at
  `/etc/crowdsec/config.yaml.local` as today. The repo file stays as the
  template.
- `app/services/crowdsec/capi.py` renders it: always the existing
  `auto_registration` block; plus, when enabled,

  ```yaml
  api:
    server:
      online_client:
        credentials_path: /etc/crowdsec/online_api_credentials.yaml
        sharing: true
        pull:
          community: true
          blocklists: true
  ```

The task `app.tasks.crowdsec.apply_capi(enabled)`:

1. Read the current file. Render the new one. Same content → `ok=true`,
   nothing restarted.
2. Write the new file.
3. If enabling and `test -s /etc/crowdsec/online_api_credentials.yaml` fails
   in the container, run `cscli capi register -f
   /etc/crowdsec/online_api_credentials.yaml`. The override is already on
   disk, so the merged config has the block and `cscli` agrees to register.
   A failure (no outbound internet) restores the previous file and records
   the error; nothing is restarted.
4. `restart()`, then `healthy()`; when enabling, also `cscli capi status`
   must exit 0 and its output must contain "successfully interact".
5. On any failure after the write: restore the previous file, `restart()`,
   record `ok=false`.

The container env keeps `DISABLE_ONLINE_API=true`. Nothing changes for a
deployment that never flips the switch. Credentials live in the CrowdSec
config volume and survive the switch going off, so turning it on again does
not register a second identity.

## What the Updates tab shows

A fifth tab, **Updates**, icon `RefreshCw`, two cards.

**Hub updates card**

- Switch "Update detection rules automatically".
- Frequency (Daily / Weekly), weekday when weekly, hour — shown in local
  time with the UTC equivalent beside it.
- Save, disabled until something changed (the pattern the ban-page card
  uses).
- **Update now** with a confirm dialog: "This checks the CrowdSec hub for
  newer rules. If anything changed, CrowdSec restarts and protected hosts
  deny traffic for a few seconds." Disabled while a run is in progress.
- Status line from the `hub_update` row: "Last run <time>, <n> items
  updated, CrowdSec restarted" / "no changes" / the error, and the agent
  line "CrowdSec v1.6.4" plus "v1.8.0 is available — rules that need it
  are skipped" when the hub said so. Polls every 5 s while a run is open.

**Community blocklist card**

- Switch "Use the CrowdSec community blocklist", with a confirm dialog
  either way: "CrowdSec restarts and protected hosts deny traffic for a few
  seconds." Enabling adds: "This registers this instance with CrowdSec's
  central service."
- State line: desired vs achieved. "On" / "Off" / "Turning on…" /
  "Failed: <error> — the previous configuration was restored", with Retry.
- One sentence noting that the blocklist refreshes itself every two hours
  once on.

When reloads are not configured (HA without a control node), both cards
render their controls disabled with the whitelist banner's explanation.

## API

- `GET /settings` — `InstanceSettingsRead` gains the five fields.
- `PATCH /settings/crowdsec-hub` — `CrowdSecHubUpdate(auto_update,
  frequency, weekday, hour_utc)`; validation 422; audit `update`.
- `PATCH /settings/crowdsec-capi` — `CrowdSecCapiUpdate(enabled)`; saves the
  desired state, enqueues `apply_capi`, 202; 409 when reloads are not
  configured (desired state is still saved, as whitelists are). Audit.
- `GET /crowdsec/maintenance` — `{hub: JobRunRead | null, capi: JobRunRead |
  null, reload_configured: bool, running: {hub: bool, capi: bool}}`.
- `POST /crowdsec/hub/update` — 202 and enqueues `update_hub("manual")`;
  409 if the lock is held or reloads are not configured. Admin only. Audit.

## Compose changes

- **Dev:** the worker gains `/var/run/docker.sock:ro` and a
  `crowdsec_data:/data/crowdsec` volume; `data-init` seeds
  `/data/crowdsec/config.yaml.local` from the template and the whitelist
  file as HA does; CrowdSec mounts that file instead of the repo file.
- **HA:** `data-init` also seeds `config.yaml.local`; CrowdSec mounts
  `${SHARED_DATA_PATH}/crowdsec/config.yaml.local` instead of the repo file.
- **Both:** `USE_WAL: "true"` on the CrowdSec service.
- `docs/crowdsec.md` — the CAPI row and the whitelist section updated; a new
  section for the Updates tab.

## Error handling

| situation | where | result |
| --- | --- | --- |
| docker socket unreachable | any step | recorded error naming container and socket; nothing else attempted |
| `hub update`/`upgrade` non-zero | job | error = last 20 lines of output; no restart |
| unhealthy after restart | hub job | rollback from tarball, restart, error recorded |
| `capi register` fails | capi job | previous file restored, no restart, error recorded |
| unhealthy or `capi status` fails | capi job | previous file restored, restart, error recorded |
| run already in progress | Update now | 409 "An update is already running." |
| reloads not configured (HA, no control node) | both | 409 with the whitelist wording; settings still saved |
| invalid schedule values | PATCH | 422 |

## Testing

**Backend**

- `test_crowdsec_hub.py` — pure: version diff (changed, new, removed
  ignored), agent-version warning parse, `hub upgrade` output tail, due-check
  against a fixed clock for daily, weekly, wrong hour, already-ran-this-hour,
  auto-update off.
- `test_crowdsec_capi.py` — pure: render with and without the block;
  `capi status` parse; idempotent when unchanged.
- `test_crowdsec_exec.py` — `exec_in_container` against an httpx
  `MockTransport`: create/start/inspect sequence, non-zero exit, socket
  error wording.
- `test_crowdsec_update_tasks.py` — the two flows with injected `exec`,
  `restart`, `healthy` fakes: no change means no restart; change means
  restart; unhealthy means rollback tar and second restart; register
  failure restores the file; lock held means no-op; every outcome lands in
  `crowdsec_job_run`.
- `test_crowdsec_maintenance_api.py` — the settings patches, the status
  endpoint, Update now 202/409, admin-only, audit rows.

**Frontend**

- `updates-tab.test.tsx` — schedule form dirty/save; local–UTC hour
  conversion; Update now confirm then call; running state disables the
  button; blocklist switch confirm text differs by direction; failed state
  shows the error and Retry; disabled with the explanation when reloads are
  not configured.

## Files

**Backend**

- `app/models/instance_settings.py`, `app/models/crowdsec_job_run.py` (new),
  `app/models/enums.py`, `alembic/versions/0030_crowdsec_updates.py`
- `app/services/crowdsec/reload.py` — `exec_in_container`
- `app/services/crowdsec/hub.py` (new), `app/services/crowdsec/capi.py` (new),
  `app/services/crowdsec/job_run.py` (new: read/record rows)
- `app/tasks/crowdsec.py` — `hub_update_tick`, `update_hub`, `apply_capi`
- `app/core/celery_app.py` — beat entry, HA routes
- `app/core/config.py` — `crowdsec_config_local_path`
- `app/schemas/instance_settings.py`, `app/schemas/crowdsec.py`
- `app/api/routes/settings.py`, `app/api/routes/crowdsec.py`
- `openapi.json`

**Frontend**

- `src/lib/api/resources/settings.ts`, `src/lib/api/resources/crowdsec.ts`
- `src/components/security/updates-tab.tsx` (new), `hub-updates-card.tsx`
  (new), `blocklist-card.tsx` (new), `security-view.tsx` — the tab

**Infra**

- `docker-compose.dev.yml`, `docker-compose.ha.yml`, `docs/crowdsec.md`

## Non-goals

- Managing which collections are installed from the UI.
- Upgrading the CrowdSec agent itself; the image stays pinned.
- Console enrolment (`ENROLL_KEY`) and console-managed blocklists beyond
  what `pull.blocklists: true` already gives.
- Per-node CrowdSec instances; there is one, on the control-plane node.

## Open risks

- **Rollback is best effort.** The tarball restores every symlink and every
  file that existed; it cannot remove files the upgrade added. A hub item
  that fails to load after an upgrade is caught by the health wait and rolled
  back, but a subtler regression (a rule that loads and misfires) is not.
- **The pinned agent.** Hub items already require newer agents than v1.6.4
  for some rules, and `hub upgrade` skips them silently. The status line
  surfaces the newer-agent warning so the operator knows to bump the image.
- **Fail-closed windows.** Every restart denies traffic on protected hosts
  for a few seconds. The default slot is 03:00 UTC and the UI says it before
  every manual action.
- **Outbound internet.** Both jobs need it from the CrowdSec container; an
  air-gapped deployment sees a recorded error each run and should switch
  auto-update off.
- **Identity.** Enabling the blocklist registers with CrowdSec's central
  service and, with `sharing: true`, sends this instance's alerts to it. The
  confirm dialog says so.
