# CrowdSec hub mirror fallback — design

## Why

On a fresh install CrowdSec crash-loops:

```
crowdsec init: while loading acquisition config: /etc/crowdsec/acquis.d/appsec.yaml:
datasource of type appsec: unable to load appsec_config: no appsec-config found for crowdsecurity/appsec-default
```

A fresh CrowdSec has nothing installed. It downloads `crowdsecurity/appsec-default`
and everything else in `COLLECTIONS` from the hub, `cdn-hub.crowdsec.net`, on its
first start. From the user's network that address answers 403 "Request blocked"
(CloudFront). This is the same kind of IP block CrowdSec's servers applied to the
office IP before. `infra/crowdsec/start.sh` sees the hub is unreachable and skips
the hub steps. That keeps an existing install running, but a fresh one has nothing
to start on.

The same hub content is published on GitHub, at
`https://raw.githubusercontent.com/crowdsecurity/hub/<branch>/...`. That copy
answers from the same network. It has the `v1.8.1` branch CrowdSec asks for, and
both WAF collections still list `appsec-default`.

## Goal

A fresh install starts on a network where the official hub refuses us, as long as
GitHub answers. An existing install behaves as it does today.

Non-goals: installs with no internet at all, and shipping a copy of the hub.
Anti-goal: starting without the WAF. The WAF fails closed, so every protected
site would deny traffic.

## Approaches

- **A. Fall back to the GitHub copy of the hub (picked by the user).** Point CrowdSec at
  GitHub only when the official hub refuses. Rules stay current.
- B. Ship the hub content inside MegooPM. This would also cover offline installs,
  but the copy goes stale, it is hundreds of files, and CrowdSec treats the rules
  as local ones it won't update. Not needed: the user's servers have internet.
- C. Start without the WAF until the hub answers. Rejected: see the anti-goal.

## Design

All changes are in `infra/crowdsec/start.sh`, the wrapper that runs before the
image's own `/docker_start.sh`.

- **Where CrowdSec downloads from.** CrowdSec v1.8.1 reads the hub address from
  `cscli.__hub_url_template__` in its config. The default is
  `https://cdn-hub.crowdsec.net/crowdsecurity/%s/%s`. The mirror is
  `https://raw.githubusercontent.com/crowdsecurity/hub/%s/%s`: the branch, then
  the path. The setting is not documented. The wrapper writes it to
  `/etc/crowdsec/config.yaml`, which lives in the writable `crowdsec_config`
  volume. It cannot use `config.yaml.local`, which is read-only and owned by the app.
- **Each start**, in `auto` mode:
  1. Official hub answers: remove the setting if an earlier start wrote it, then
     run the hub steps as today.
  2. Official hub refuses, GitHub answers: write the mirror setting, log
     `megoopm: official hub unreachable, using the GitHub mirror`, then run the hub
     steps as today in online mode. A failed step still only logs, as now.
  3. Neither answers: skip the hub steps as today. If the WAF config
     `appsec-default` is not installed (a fresh install), log a clear message
     naming both addresses and the network cause, then exit non-zero. Docker
     restarts the container, so it retries until a hub answers. This replaces
     CrowdSec's own fatal message with one that says what to fix.
- **Check that the setting took effect.** After writing it, the wrapper reads it
  back through `cscli config show`. If CrowdSec ignores it (a future version
  renamed it), the wrapper logs a warning saying so. Starting then behaves as in step 3.
- **Probe addresses** stay as constants in the script. Like the existing seams,
  they can be overridden by environment variables so tests can point them at
  stubs.
- `MEGOOPM_HUB_MODE=online|offline` keep their meaning. `online` forces the
  official hub; the mirror logic only runs in `auto`.
- The header comment's "a first start still needs the hub" limit is updated to
  say a first start needs the official hub or GitHub.

The mirror setting stays in `config.yaml` until the next start finds the official
hub again. Meanwhile MegooPM's own hub auto-update, which runs `cscli` in the
container, also uses the mirror. This is intended, since the official hub refuses
those calls too.

## Tests

- Extend `backend/tests/test_crowdsec_start_script.py` (it runs `start.sh` against
  the pinned `docker_start-v1.8.1.sh` fixture). Cover the three cases above, plus
  "setting written, then removed when the official hub returns" and "neither hub,
  but already installed, so it starts as today".
- The config edit and the probes go through seams the tests can stub, such as a
  fake `yq`/`cscli` on `PATH` or an env override, so the tests don't need the image.

## Left to the builder

The exact seam names, how "appsec-default installed" is detected (for example
the file under `/etc/crowdsec/appsec-configs/` or `cscli appsec-configs inspect`),
and the log wording, within the meaning above. Docs: one paragraph in
`docs/crowdsec.md` on the fallback.

## Calls I made (not discussed)

- Mirror only in `auto` mode; `online`/`offline` overrides unchanged.
- Fail with a clear message on a fresh install with no hub, instead of letting
  CrowdSec's fatal error loop.
- Don't pin `hub_branch`. CrowdSec still picks the branch from
  `version.crowdsec.net`, which answers from the user's network today.
