# CrowdSec 1.8 upgrade and the WAF upload limit — design

**Status:** awaiting review, 2026-10-25. The Settings tab mockup, the
defaults and the always-restart behaviour with its safety net were agreed
in the brainstorm.

## Intent

Move the bundled CrowdSec engine from v1.6.4 to the current release (v1.8.1),
without breaking large uploads through hosts that use the WAF.

Newer CrowdSec buffers each request body in memory before its AppSec (WAF)
rules run, up to **10 MB by default**, and by default **blocks** any request
whose body is larger, answering with the ban remediation (CrowdSec docs,
AppSec hooks: `SetMaxBodySize`, `SetBodySizeExceededAction`, default
`drop`). The user runs Nextcloud behind MegooPM and uploads big files; its
clients send chunks and whole files well over 10 MB. After a plain upgrade,
those uploads would hit the ban page on every Nextcloud host with the WAF
switched on — silently, from the user's side.

What the user asked for: upgrade to v1.8, and a new **Settings** tab on the
Security page where an admin can set the upload size limit at any time, and
choose what happens to a bigger request — `drop`, `partial` or `allow` — with
each choice explained.

Success looks like: after the upgrade, an admin opens Security → Settings,
sets a limit and an action that suit Nextcloud, saves, and large Nextcloud
uploads through a WAF-enabled host work; the admin understood what each
choice trades away before picking it.

## Goals

1. Upgrade the CrowdSec image to `crowdsecurity/crowdsec:v1.8.1` in all three
   compose files (`docker-compose.yml`, `docker-compose.dev.yml`,
   `docker-compose.ha.yml`), with every MegooPM integration that talks to the
   engine still working (see *Upgrade work*).
2. A **Settings** tab on the Security page, admin-only, after Updates.
3. On it, one card — **WAF upload limit** — with:
   - a size field and an MB/GB unit picker;
   - the three over-limit actions as a radio group, each with a one-line
     explanation and a gain/cost line (copy below);
   - which hosts the setting affects (see *Which hosts it affects*);
   - a memory note when the size is large;
   - **Save and restart CrowdSec**, behind a confirmation dialog;
   - a status line: what is live now, and the last apply's outcome.
4. Applying is safe in the way whitelists already are: validate, restart,
   wait for health, and **restore the previous file and restart again** if
   CrowdSec does not come back. An unchanged value restarts nothing.

## Non-goals

- Per-host limits. CrowdSec's limit is engine-wide (an `on_load` hook); there
  is no per-site setting to expose. Per-path exemptions
  (`DisableBodyInspection` in a `pre_eval` hook) are a possible later feature,
  not this one.
- Any other WAF or CrowdSec setting on the new tab. The tab is named for
  growth, but this project puts one card on it.
- nginx's own `client_max_body_size` (unlimited today, per-host override in
  advanced config). Unchanged.
- Upgrading anything else in the stack.

## Anti-goals

- Nextcloud uploads failing after the upgrade with nothing in the UI pointing
  at the cause.
- A value that CrowdSec cannot load taking the engine down. Every protected
  host fails closed (`APPSEC_FAILURE_ACTION=deny`) while it is down.
- Restarting CrowdSec when nothing changed.
- Copy that reads like CrowdSec jargon. The choices are explained in terms of
  what happens to the visitor's upload.

## Constraints

- **CrowdSec runs once.** Single node: one container. HA: only on the
  control-plane node (`profiles: ["control-plane"]`); the apply task is routed
  to `CROWDSEC_CONTROL_NODE_ID`'s queue like the whitelist and CAPI tasks, and
  is refused with 409 when reloads are not configured.
- **A restart is the only way to apply.** The limit is set in an `on_load`
  hook, which runs when the AppSec config loads. Protected hosts deny traffic
  for a few seconds.
- **A restart needs CrowdSec's central API when the community blocklist is
  on.** CrowdSec treats a CAPI rejection as fatal at startup. The deployment
  that prompted this sits behind an office address CrowdSec's firewall blocks
  intermittently (see `docs/crowdsec.md`, "When CrowdSec is blocking the
  address"). The upgrade itself recreates the container, so it must be done
  while that block has lifted, or with the blocklist off.
- **Single-file mounts.** App-authored CrowdSec files are single FILE mounts
  out of the shared data path: an `app_data` volume `subpath` in production
  and dev, a `${SHARED_DATA_PATH}` host bind in HA. They are seeded by
  `data-init`, and rewritten in place (never replaced), because a bind mount
  pins the inode it saw at start. The new file follows the same rules.
- **The setting needs the new engine.** v1.6.4 has neither helper, so the
  feature and the upgrade ship together.

## Decided in the brainstorm

- Location: a new **Settings** tab on the Security page.
- Controls: a size field, and a choice of the three actions — Block it
  (`drop`), Check the first part (`partial`), Let it through without
  checking the body (`allow`) — each explained.
- The approved mockup: `.superpowers/brainstorm/1629-1792917029/content/settings-tab-v1.html`
  (layout, copy, confirmation dialog, failure line). Copy for the three
  choices, as approved:

  | Choice | Explanation | Gain | Cost |
  | --- | --- | --- | --- |
  | Block it · `drop` · CrowdSec's default | The request is refused with the ban page, and its body is never checked. | Nothing gets past the WAF unchecked | Uploads bigger than the limit fail |
  | Check the first part · `partial` | The first {limit} is checked against the WAF rules, and the rest passes through unchecked. | Big uploads work; most of the body is still checked | An attack hidden past the limit is missed |
  | Let it through without checking the body · `allow` | The body is skipped entirely. The address, URL, query and headers are still checked. | Big uploads work, at the lowest memory cost | Nothing in an oversized body is checked |

- **Defaults: 10 MB, Check the first part (`partial`).** Not CrowdSec's own
  default (10 MB, `drop`), which would break large Nextcloud uploads the
  moment the upgrade lands. 10 MB keeps CrowdSec's memory use where its
  standard setting puts it, and `partial` keeps uploads working while the
  first 10 MB of every body is still checked. These are the migration's
  column defaults and the seeded file's values, so every install — fresh or
  upgraded — starts here.
- **Save always restarts, block or not — with a safety net.** The user cannot
  wait for CrowdSec to lift a block: the setting must take effect at once. The
  danger is real but narrower on v1.8.1: at startup it reuses the CAPI token
  saved in its database (`apic.Authenticate`, "using valid token from DB") and
  only logs in when that token is missing or expired. If it must log in while
  the address is blocked, startup fails and every protected host denies all
  traffic. So when CrowdSec does not come back from the apply's restart **and**
  the community blocklist is on **and** CrowdSec's firewall is blocking the
  address (`capi.probe_edge_block()` asked at that moment), the task switches
  the blocklist off — `config.yaml.local` rendered without `online_client`,
  exactly as the Blocklist card's off switch writes it — keeps the new body-size
  file, and restarts again. Without the blocklist CrowdSec needs no CAPI at
  startup, so it comes back with the new limit in force. The switch-off is
  recorded like an off switch from the Blocklist card (desired state off, a
  `capi_apply` run whose detail says why), so that card reads "Off" with the
  reason — switched off because CrowdSec's servers were blocking this address
  — and the admin turns it back on once the block lifts. Only if CrowdSec
  still does not come back does the ordinary rollback run.
- **The confirmation dialog warns** when the last check recorded the address
  as blocked and the blocklist is on: the restart may switch the community
  blocklist off to get CrowdSec back.

## How it works

### The CrowdSec side

A MegooPM-owned AppSec config, loaded after the hub's default one:

```yaml
# /etc/crowdsec/appsec-configs/megoopm-body-size.yaml — managed by MegooPM
name: megoopm/body-size
on_load:
  - apply:
      - SetMaxBodySize(104857600)          # bytes
      - SetBodySizeExceededAction("partial")
```

and the acquisition file (`infra/crowdsec/acquis/appsec.yaml`) moves from
`appsec_config: crowdsecurity/appsec-default` to:

```yaml
appsec_configs:
  - crowdsecurity/appsec-default
  - megoopm/body-size
```

With several configs, hooks are appended and run in list order, and
conflicting options take the last one listed (CrowdSec docs, "Defining
Multiple AppSec Configurations"). Ours is last, so it wins.

The file lives in the data path (`crowdsec/appsec/megoopm-body-size.yaml`,
say), is seeded by `data-init` when missing, and is mounted read-only at
`/etc/crowdsec/appsec-configs/megoopm-body-size.yaml` in all three compose
files.

*My call* on how the file and the database stay in step: the backend renders
the file from the database on every apply; `data-init` seeds it from a repo
template (like `infra/crowdsec/config.yaml.local`) holding **the same values
as the migration's column defaults**, so a fresh or upgraded install starts
consistent without an automatic restart. Nothing reconciles on boot. Before
the first apply, the status line reads the defaults as live ("Live: … —
the default").

### The nginx side

The Lua bouncer MegooPM builds into nginx (`CS_BOUNCER_VERSION=v1.0.8` in
`infra/nginx/Dockerfile`) reads the **whole** request body into Lua memory
(`fh:read("*a")`) before forwarding it to AppSec. A multi-GB Nextcloud upload
through a WAF-enabled host would therefore exhaust the nginx worker's memory
no matter what CrowdSec's limit says. `lua-cs-bouncer` **v1.0.19**
(2026-09-11) streams a file-backed body to AppSec in 64 KB chunks instead.
Bump it to v1.0.19 as part of this project, and recheck the config keys our
`crowdsec-bouncer.conf` template uses against that version.

Verify on v1.0.19 that AppSec is still global, as the section on affected
hosts assumes.

With the streaming bouncer, the memory cost is CrowdSec's: up to the limit,
per request in flight. The memory note on the card says that (it replaces the
mockup's "nginx and CrowdSec each hold the body" wording).

### Storage and API

- `instance_settings` gains the desired values: `crowdsec_appsec_max_body_bytes`
  (BIGINT) and `crowdsec_appsec_body_exceeded_action` (enum `drop` /
  `partial` / `allow`). One migration.
- The outcome of applying is a `crowdsec_job_run` row of a new kind (say
  `appsec_settings`), with the trigger, `ok`, `error`, `restarted`, and the
  values applied in `detail` — the same shape the Updates tab reads for hub
  and CAPI runs, so "desired vs live" can be shown when they differ.
- A `PATCH` on the settings router (like `crowdsec-capi`) stores the desired
  values and enqueues the apply task; `202`, `409` while an apply runs or
  when reloads are not configured. A read endpoint (or the existing
  maintenance payload) returns desired values, last run, and the WAF-enabled
  host list for the card.
- Validation: whole MB, **1 to 100 MB** — *amended during implementation*: the
  original 2 GB ceiling was disproved; Coraza rejects any body of 128 MiB or
  more on its own (see docs/crowdsec.md, "Why 100 MB is the ceiling"). The
  original reasoning was (my call: above that, a single
  request can hold gigabytes of CrowdSec's memory, and `partial`/`allow`
  already cover larger uploads). The action is one of the three.

### The apply task

Mirror `apply_whitelists_to_disk`: render → compare with what is on disk and
skip if identical → write in place → `crowdsec -t` (`CMD_CONFIG_TEST`) →
restart → wait for LAPI → on failure restore the previous content and
restart again. Run it under a Redis lock; record the run.

The safety net (see *Decided in the brainstorm*) sits between the failed
health wait and the rollback:

1. Restart → healthy: done.
2. Not healthy, blocklist on, `probe_edge_block()` is `True`: write
   `config.yaml.local` without `online_client`, restart → healthy: done,
   recorded as applied **with** the blocklist switched off and why. The task
   takes the CAPI lock as well as its own, since it rewrites the file the CAPI
   apply owns.
3. Otherwise, or if step 2 is not healthy either: restore the previous
   body-size file (and the previous `config.yaml.local`, if step 2 changed
   it) and restart — the ordinary rollback, reported as failed.

The probe is asked only after a failed restart, never on every save: an
apply that comes back healthy costs no request to CrowdSec's servers.

No other path that restarts CrowdSec (whitelists, hub upgrade, CAPI apply)
has this net today. Extending it to them is a sensible follow-up, outside
this project.

### The tab

`SecurityView` gains a `settings` tab (admin-only, like `updates`) rendering a
`WafSettingsCard`. Build it from the mockup with the existing card idiom
(`HubUpdatesCard`, `BlocklistCard`): `Card`, `Label`, `Select`, a radio group,
`ConfirmDeleteDialog` for the confirmation, the job-run status line.

### Which hosts it affects

AppSec in MegooPM is **global, not per host** (`docs/crowdsec.md`, "AppSec
scope: global, not per host (yet)"): while `CROWDSEC_APPSEC_URL` is set, the
Lua bouncer inspects every request on every server that enforces CrowdSec.
The per-host `crowdsec_appsec_enabled` flag is a reserved marker that nothing
enforces, and the UI has no toggle for it. So, *my call*, correcting the
mockup:

- The card lists every host with **CrowdSec on** (`crowdsec_enabled`) — proxy,
  redirection and dead hosts — not hosts with the per-host AppSec flag, which
  would show "0 hosts" while every Nextcloud host is in fact inspected.
- The card copy says "every host protected by CrowdSec", not "every proxy
  host with the WAF switched on".
- When `CROWDSEC_APPSEC_URL` is blank, the WAF is off: the card says so, and
  the limit can still be saved but has no effect until AppSec is wired.

Making AppSec genuinely per host stays a non-goal: it needs Lua changes and a
host toggle, and the user asked for one engine-wide limit.

## Upgrade work (1.6.4 → 1.8.1)

The builder has to verify each of these against v1.8.1, not assume:

- **Image tag** in the three compose files. `/var/lib/crowdsec/data` must be a
  persisted volume from 1.7.0 on — it already is (`crowdsec_data`).
- **What MegooPM parses from `cscli`:** `cscli version` (`parse_agent_version`),
  `cscli hub list -o json` (`parse_hub_list`), the release notice
  (`_AGENT_WARNING`), `cscli capi status`'s success line (`parse_capi_status`),
  `cscli capi register`, `crowdsec -t` (`config_test_error`), and the hub
  backup/restore item directories (`HUB_ITEM_TYPES`). Any changed format is a
  silent failure in the Updates and Blocklist cards.
- **Hub items:** `COLLECTIONS` still resolve, and `crowdsecurity/appsec-default`
  still exists under that name.
- **LAPI client:** the backend's `CrowdSecClient` (decisions, alerts, metrics,
  machine auto-registration) against the new LAPI.
- **Ban page / remediation:** a WAF block still returns the JSON the Lua
  bouncer decodes into the MegooPM ban page.
- **Docs:** `docs/crowdsec.md` (version references, the new file, the setting,
  the restart caveat); `HA-SETUP.md` if the upgrade order on the control-plane
  node needs a note.

Testing note: the dev stack starts its own CrowdSec, which downloads from the
hub. On the office connection that CrowdSec is blocking, that adds requests
from a blocked address and the downloads fail anyway. Test the upgrade from a
connection that is not blocked.

## Left to the builder

- File and module names, the endpoint shapes, the job-run kind's name.
- Whether the card's data rides on `/crowdsec/maintenance` or gets its own
  read endpoint.
- Exact memory-note threshold (the mockup uses 100 MB) and its wording.
- Whether the new file is included in backups alongside the whitelist and
  `config.yaml.local`, or re-rendered from the database after a restore —
  whichever matches how the backup feature treats those two today.
- Test layout, following the existing CrowdSec tests (pure apply function
  with callables, task tests with fakes, API tests, card tests).
