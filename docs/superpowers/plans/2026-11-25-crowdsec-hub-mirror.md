# CrowdSec hub mirror fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A fresh install's CrowdSec starts when the official hub refuses the address but GitHub's copy of the hub answers. With neither, it stops with a clear message instead of CrowdSec's fatal AppSec error.

**Architecture:** `infra/crowdsec/start.sh` already rewrites the image's `/docker_start.sh` before running it. It will also:
- decide a hub state (`official`, `mirror` or `none`);
- write a small hooks file that holds that state;
- splice two hook calls into the patched script:
  - one before the stock `prepare_hub` call, which sets or clears CrowdSec's hub URL template;
  - one before `exec crowdsec`, which refuses to start without `appsec-default`.

The hooks run inside the stock script. That is because on a fresh volume, `config.yaml` only exists after the stock script populates `/etc/crowdsec`.

**Tech Stack:** bash, sed, yq (in the image, via the stock `conf_set`), cscli; pytest, which runs the wrapper with fake `wget`/`cscli`/`crowdsec` on `PATH`.

**Spec:** `docs/superpowers/specs/2026-11-25-crowdsec-hub-mirror-design.md`

## Global Constraints

- Every behaviour change is in `infra/crowdsec/start.sh`. Compose files and the image stay as they are.
- Mirror template, exactly: `https://raw.githubusercontent.com/crowdsecurity/hub/%s/%s`. The setting key is `.cscli.__hub_url_template__` in `$CONFIG_FILE` (`/etc/crowdsec/config.yaml`). Never write it to `config.yaml.local`, which is mounted read-only.
- Official probe: `https://cdn-hub.crowdsec.net/crowdsecurity/master/.index.json` (unchanged). Mirror probe: `https://raw.githubusercontent.com/crowdsecurity/hub/master/.index.json`. Both are overridable through `MEGOOPM_HUB_PROBE_URL` / `MEGOOPM_MIRROR_PROBE_URL`. Probes stay headers-only (`wget -q --spider`, `timeout 15`, `-T 5`).
- Mirror log line, exactly: `megoopm: official hub unreachable, using the GitHub mirror`.
- The mirror logic runs only for `MEGOOPM_HUB_MODE=auto` (the default). `online` means the official hub with no probing. `offline` means the hub steps are skipped and the setting is left alone.
- Don't pin `hub_branch`.
- The wrapper keeps `set -euo pipefail`. A hook that fails (for example, `conf_set` errors) logs and carries on. Only the missing-`appsec-default` guard exits non-zero.
- Rewrites use only `sed -E 's|…|…|'` substitutions, with no `a`/`i` commands and no newlines in replacements. The real image's sed may not be GNU sed.
- Backend tests run in the Linux test container: `sh /c/Users/hammad/AppData/Local/Temp/claude/4ce1bd210930642f/24e53b7e-1597-4e14-ad60-5cf76c44ba8b/scratchpad/t.sh tests/test_crowdsec_start_script.py -q` (from the repo root; it runs pytest in `backend/`).

## Review Focus

1. **A user's own hub URL template.** An install whose `config.yaml` already sets `__hub_url_template__` to a URL of their own keeps it when the official hub answers. The clean-up deletes only the value we wrote. Pinned by `test_official_hub_only_clears_our_own_mirror_setting` (Task 1).
2. **Real stock script.** The `prepare_hub` and `exec crowdsec` anchors each appear exactly once in the pinned v1.8.1 script. After the rewrite, `bash -n` passes on it, and the hook calls sit right before them. If a future image drops an anchor, the wrapper logs that and starts as today. Pinned by `test_the_real_script_has_the_anchors_the_wrapper_hooks` (Task 2) and `test_a_script_without_the_anchors_still_starts` (Task 1).
3. **CrowdSec ignoring the setting.** The read-back finds no mirror, so the wrapper warns that the setting was ignored, then still runs the steps (they fail softly). After that the guard decides. Pinned by `test_an_ignored_mirror_setting_is_reported` (Task 1).
4. **`%s` survives.** The template reaches `conf_set` with both `%s` intact, not eaten by printf or sed. Pinned by the exact-string assertion in `test_a_blocked_hub_switches_to_the_github_mirror` (Task 1).
5. **An existing install with no hub at all** still starts on its installed items, exactly as today. Pinned by `test_an_installed_waf_config_starts_without_any_hub` (Task 2).

---

### Task 1: Hub state, hooks file and the mirror setting

**Files:**
- Modify: `infra/crowdsec/start.sh`
- Test: `backend/tests/test_crowdsec_start_script.py`

**Interfaces:**
- Produces, for Task 2:
  - Env seams: `MEGOOPM_HUB_PROBE_URL`, `MEGOOPM_MIRROR_PROBE_URL`, `MEGOOPM_HOOKS` (default `/tmp/megoopm-hooks.sh`).
  - The hooks file is a bash file that sets `megoopm_hub_state` (`official|mirror|none|offline`), `megoopm_hub_probe`, `megoopm_mirror_probe` and the `log` function, and defines `megoopm_before_hub`.
  - The patched script sources the hooks file on the line it rewrites. The stock bare line `prepare_hub` becomes `. '<hooks>'; megoopm_before_hub; prepare_hub`.
  - In the test module: the helper `_fake_bin(tmp_path) -> Path` and `_run(..., env=None)`, which accepts extra env.

- [ ] **Step 1: Add the test seams and write the failing tests.**

  Test seams:
  - `_run` gains `env: dict | None = None`, which is merged into the environment it builds. It always sets `MEGOOPM_HOOKS=str(tmp_path / "hooks.sh")`.
  - `_fake_bin(tmp_path)` writes executable scripts into `tmp_path/"bin"`:
    - `wget` appends its last argument to `$FAKE_WGET_LOG` (if set). It exits 0 when that argument is one of the space-separated `$FAKE_WGET_UP`, and 8 otherwise.
    - `cscli` prints `$FAKE_HUB_TEMPLATE`.
    - `crowdsec` prints `crowdsec running`.
  - Tests prepend the bin directory to `PATH`.
  - Constants: `OFFICIAL = "https://hub.test/official"` and `MIRROR = "https://hub.test/mirror"`, passed as the two probe env vars.

  New stock snippet `MIRROR_STOCK`. Its shape is the real one: the hub steps live inside `prepare_hub`.

  ```bash
  #!/bin/bash
  set -e
  conf_set() { echo "conf_set $1"; }
  cscli_if_clean() { echo "installed $*"; }
  prepare_hub() {
      cscli_if_clean collections install crowdsecurity/appsec-virtual-patching
  }
  prepare_hub
  exec crowdsec $ARGS
  ```

  Tests (mode `auto` unless named):
  - `test_a_blocked_hub_switches_to_the_github_mirror`: up=MIRROR, template=`https://raw.githubusercontent.com/crowdsecurity/hub/%s/%s`.
    - rc 0.
    - stdout contains `conf_set .cscli.__hub_url_template__ = "https://raw.githubusercontent.com/crowdsecurity/hub/%s/%s"`, and it comes before `installed collections install`.
    - stderr contains `megoopm: official hub unreachable, using the GitHub mirror`.
    - stdout ends with `crowdsec running`.
  - `test_the_official_hub_runs_as_before`: up=OFFICIAL.
    - The steps run.
    - No `using the GitHub mirror` in stderr.
    - The wget log shows only OFFICIAL, so the mirror was never probed.
  - `test_official_hub_only_clears_our_own_mirror_setting`: up=OFFICIAL.
    - stdout contains exactly `conf_set del(.cscli.__hub_url_template__ | select(. == "https://raw.githubusercontent.com/crowdsecurity/hub/%s/%s"))`.
  - `test_an_ignored_mirror_setting_is_reported`: up=MIRROR, template=`https://cdn-hub.crowdsec.net/crowdsecurity/%s/%s`.
    - stderr contains `ignored the hub mirror setting`.
    - The steps still run.
  - `test_no_hub_skips_the_steps_and_keeps_the_setting`: up empty.
    - No `conf_set` in stdout.
    - stderr contains `skipped, hub unreachable`.
    - The steps did not run.
  - `test_online_mode_never_probes`: mode `online`, wget log set.
    - The log file is absent or empty.
    - The steps run.
    - The `del(...)` clean-up line is printed.
  - `test_offline_mode_leaves_the_setting_alone`: mode `offline`.
    - No `conf_set` in stdout.
  - `test_a_script_without_the_anchors_still_starts`: stock is `MIRROR_STOCK` with the bare `prepare_hub` line replaced by `prepare_hub  # call`, so the anchor misses.
    - rc 0.
    - stderr contains `no prepare_hub call found`.
    - stdout ends with `crowdsec running`.

- [ ] **Step 2: Run them and watch them fail.**
  Run the t.sh command with `-k "mirror or official or ignored or no_hub or online_mode or offline_mode or anchors"`.
  Expected: the new tests FAIL. The existing tests still pass.

- [ ] **Step 3: Implement in `infra/crowdsec/start.sh`.**
  - Replace the probe constant with `hub_probe=${MEGOOPM_HUB_PROBE_URL:-https://cdn-hub.crowdsec.net/crowdsecurity/master/.index.json}` and `mirror_probe=${MEGOOPM_MIRROR_PROBE_URL:-https://raw.githubusercontent.com/crowdsecurity/hub/master/.index.json}`. Add `mirror_template='https://raw.githubusercontent.com/crowdsecurity/hub/%s/%s'` and `hooks=${MEGOOPM_HOOKS:-/tmp/megoopm-hooks.sh}`.
  - Add `probe <url>`. It holds today's wget line, including the missing-wget fallback, which returns 0.
  - `hub_state` echoes the state:
    - `online` gives `official`, `offline` gives `offline`;
    - in auto mode, `probe hub_probe` gives `official`; otherwise `probe mirror_probe` gives `mirror`; otherwise `none`.
    - The replacement choice stays as today: `official|mirror` run the steps and log failures; `none|offline` skip them. Keep today's unreachable log line, naming `$hub_probe`.
  - Write the hooks file with a quoted heredoc, so `%s` and `$` stay literal. Inject the values from the top with separate `printf '%q'` assignment lines; never pass the template as a printf format. `megoopm_before_hub`:
    - `official`: `conf_set 'del(.cscli.__hub_url_template__ | select(. == "<template>"))'`. The value inside the select is the mirror template.
    - `mirror`:
      - log the exact mirror line;
      - `conf_set '.cscli.__hub_url_template__ = "<template>"'`;
      - read it back with `cscli config show --key Config.Cscli.HubURLTemplate 2>/dev/null || true`;
      - if the output does not contain `raw.githubusercontent.com`, log `CrowdSec ignored the hub mirror setting (has the image changed?); the hub steps will likely fail`.
    - `none|offline`: nothing.
    - Each `conf_set` is followed by `|| log "could not update the hub URL setting"`.
  - Rewrite: add `-e "s|^prepare_hub\$|. '$hooks'; megoopm_before_hub; prepare_hub|"` to the existing sed. Before that, if `grep -qx 'prepare_hub' "$stock"` fails, log `no prepare_hub call found in $stock (has the image changed?); the hub mirror is off`.

- [ ] **Step 4: Run the whole module.**
  Run the t.sh command for `tests/test_crowdsec_start_script.py -q`.
  Expected: all pass, the old tests included.

- [ ] **Step 5: Commit.**
  `git add infra/crowdsec/start.sh backend/tests/test_crowdsec_start_script.py && git commit -m "feat(crowdsec): fall back to the GitHub hub mirror when the hub refuses us"`

### Task 2: The `appsec-default` guard, real-script anchors, comments and docs

**Files:**
- Modify: `infra/crowdsec/start.sh` (the header comment at lines 1-21, the hooks file, the sed)
- Modify: `docs/crowdsec.md` (lines 64-69, and after line 700)
- Test: `backend/tests/test_crowdsec_start_script.py`

**Interfaces:**
- Consumes: Task 1's hooks file, `megoopm_hub_state`, `_fake_bin`, `_run(env=…)` and `MIRROR_STOCK`.
- Produces: `megoopm_before_crowdsec` in the hooks file. Env seam `MEGOOPM_APPSEC_DEFAULT` (default `/etc/crowdsec/appsec-configs/appsec-default.yaml`). The stock line `exec crowdsec …` becomes `. '<hooks>'; megoopm_before_crowdsec; exec crowdsec …`.

- [ ] **Step 1: Write the failing tests.**
  - `_run` defaults `MEGOOPM_APPSEC_DEFAULT` to a file it creates, `tmp_path/"appsec-default.yaml"`. This keeps Task 1's tests starting once the guard exists. A test overrides it through `env`.
  - `test_a_fresh_install_with_no_hub_stops_with_a_clear_message`: auto mode, up empty, `MEGOOPM_APPSEC_DEFAULT=tmp_path/"missing.yaml"`.
    - rc 1.
    - stderr contains `crowdsecurity/appsec-default`, OFFICIAL and MIRROR.
    - `crowdsec running` is not in stdout.
  - `test_an_installed_waf_config_starts_without_any_hub`: same, but the file exists.
    - rc 0.
    - stdout ends with `crowdsec running`.
  - `test_the_real_script_has_the_anchors_the_wrapper_hooks`: in the pinned fixture, exactly one line equals `prepare_hub`, and exactly one line starts with `exec crowdsec `.
  - Extend `test_the_real_script_rewrites_cleanly` (both modes). The patched text contains the line `. '<hooks>'; megoopm_before_hub; prepare_hub` and a line starting `. '<hooks>'; megoopm_before_crowdsec; exec crowdsec `. `bash -n` passes on the hooks file. Drop or adapt the existing `endswith("exec crowdsec $ARGS")` assertion, so the last line is the rewritten exec line.

- [ ] **Step 2: Run them and watch them fail.**
  Run the t.sh command with `-k "fresh_install or installed_waf or anchors or rewrites_cleanly"`.
  Expected: these tests FAIL.

- [ ] **Step 3: Implement.**
  - **The guard.** The hooks file gains `megoopm_before_crowdsec`, which runs in every state. If `[ ! -e "$appsec_default" ]` (a dangling hub symlink counts as missing), it logs one message and exits 1. Docker's `unless-stopped` restart then retries. The message says:
    - CrowdSec's WAF config `crowdsecurity/appsec-default` is not installed, and CrowdSec cannot start without it;
    - a first start downloads it from the official hub (`$hub_probe`) or GitHub (`$mirror_probe`);
    - the state's reason: "neither answered from this server; the official hub blocks addresses that send too many requests (403), so check that one of them is reachable" for `none`, "the hub is switched off (MEGOOPM_HUB_MODE=offline)" for `offline`, and "the hub install failed; see the lines above" otherwise.
  - **The anchor.** Add the sed `-e "s|^exec crowdsec |. '$hooks'; megoopm_before_crowdsec; exec crowdsec |"`. If `grep -q '^exec crowdsec ' "$stock"` fails, log `no exec crowdsec line found in $stock (has the image changed?); the WAF config check is off`.
  - **The header comment.** Rewrite lines 12-19 to describe the states:
    - official: the steps run, and failures are logged;
    - mirror: the same, from GitHub's copy, through `cscli.__hub_url_template__`;
    - none: the steps are skipped.
    Replace the "first start still needs the hub" sentence with: a first start needs the official hub or GitHub, and without either it stops with a message saying so. List the env seams.
  - **docs/crowdsec.md, lines 64-69.** Correct the parenthetical. GitHub's copy of the hub has a `v1.8.1` branch; pinning `cscli.hub_branch` would still remove the lookup, but it is not done, so a CrowdSec upgrade picks its own branch.
  - **docs/crowdsec.md, after line 700.** Add a paragraph, **"A first start while the hub refuses the address."** The points to cover:
    - a fresh install has no rules and must download them;
    - when the hub answers 403 but GitHub (`raw.githubusercontent.com/crowdsecurity/hub`) answers, the start uses GitHub and logs the mirror line;
    - the setting stays in `/etc/crowdsec/config.yaml` until a start finds the official hub again, and MegooPM's hub updates use it meanwhile;
    - with neither hub, the container stops with the message above and Docker retries it;
    - an existing install starts on what it has.

- [ ] **Step 4: Run the whole module.**
  Run the t.sh command for `tests/test_crowdsec_start_script.py -q`, then the whole backend suite with `sh …/t.sh -q`.
  Expected: the module passes. In the suite, the only failures are the 5 known environment-only failures in `test_compose_crowdsec_whitelists`.

- [ ] **Step 5: Commit.**
  `git add infra/crowdsec/start.sh backend/tests/test_crowdsec_start_script.py docs/crowdsec.md && git commit -m "feat(crowdsec): explain a fresh start that no hub can serve"`
