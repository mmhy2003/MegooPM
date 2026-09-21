# CrowdSec enforcement beyond proxy hosts — design

**Status:** approved in chat, 2026-09-21
**Related:** `fc3ed6a` (one active ban per IP) — shipped first, because this
change multiplies the servers answering banned clients with 403.

## Problem

CrowdSec bans are enforced only on **proxy hosts with the CrowdSec toggle on**.
Every other server block nginx runs still sends its traffic to CrowdSec, so an
IP can be *banned* for what it did there — and never be *blocked* there:

| Server block | Bouncer today |
|---|---|
| Proxy host, toggle on, :443 and plain :80 | yes |
| Proxy host, toggle on, :80 with **Force SSL** | **no** (see below) |
| Redirection hosts (:80, :443) | no |
| 404 hosts (:80, :443) | no |
| Default catch-all server (:80, base `nginx.conf`) | no |
| Default-TLS sites (one per certificate, :443) | no |

### Why simply adding today's hook would not work

The bouncer runs as `access_by_lua_file`, in nginx's **access** phase. Every
server type above answers with `return`, which runs in the **rewrite** phase —
earlier. Measured in the shipped image (`megoopm-nginx`, OpenResty 1.25.3.2):

| Setup | Response |
|---|---|
| `access_by_lua` denying + `return 302` | **302** — the bouncer never ran |
| `server_rewrite_by_lua` denying + `return 302` | **403** — the bouncer ran first |
| `server_rewrite_by_lua` passing + `return 302` | 302 — normal flow intact |
| `server_rewrite_by_lua` denying + `location / { return 404; }` | 403 |

The same bug already exists on a proxy host's :80 server with Force SSL: its
`location / { return 301 https://… }` beats the access-phase bouncer, so a
banned client is redirected to HTTPS rather than refused (it is refused there).

## Decisions (from the brainstorm)

1. **Control:** redirection and 404 hosts get a per-host CrowdSec toggle like
   proxy hosts. The default sites always enforce — their traffic is almost all
   scanners hitting names nobody configured, and there is nothing to opt out of.
2. **Default state:** the toggle is **on** for existing and new redirection and
   404 hosts. The migration switches existing rows on; the gap closes on deploy.
3. **Checks:** IP bans **and AppSec**, the same `Allow()` proxy hosts run.
   Consequence, accepted knowingly: AppSec is configured
   `APPSEC_FAILURE_ACTION=deny`, so while CrowdSec's AppSec listener is
   unreachable — an outage, or the few seconds of a CrowdSec restart after a
   whitelist apply or hub update — **every enforcing redirection host, 404 host
   and default site refuses traffic**, as protected proxy hosts already do.
   IP-ban checks alone do not have this property (decisions are cached locally
   in stream mode).
4. **Scope of the proxy-host change:** only the Force-SSL :80 server changes
   phase. Proxy hosts' other servers keep the access-phase hook they have.

## Design

### 1. The hook

New server types run the **existing** handler through
`server_rewrite_by_lua_file /etc/nginx/lua/megoopm_crowdsec.lua;`, which runs
before any `return` or `if (…) { return …; }` in the server (redirection
hosts' "Block exploits" rules are server-level `if` returns).

The handler body moves into a Lua module (`megoopm_crowdsec_check.lua`,
exposing `check()`) so two entry points can share it:

- `megoopm_crowdsec.lua` — `require("megoopm_crowdsec_check").check()`;
  used by every generated host, in either phase.
- the base default server, inline:
  `server_rewrite_by_lua_block { if ngx.var.uri ~= "/healthz" then require("megoopm_crowdsec_check").check() end }`
  — the container healthcheck is exempt, or an AppSec outage would mark nginx
  unhealthy and get it restarted.

**Uninitialised bouncer.** When CrowdSec is not configured, the handler logs
`CrowdSec bouncer not initialised; allowing request` and allows. Today that
logs once per request on hosts an operator opted in; on always-on default
sites it would log once per scanner hit. The module logs it **once per
worker** instead.

### 2. Where it is emitted

| Template | Change |
|---|---|
| `redirect.conf.j2` | every server block, when `host.crowdsec_enabled` |
| `dead.conf.j2` | every server block, when `host.crowdsec_enabled` |
| `default_tls.conf.j2` | always |
| `infra/nginx/nginx.conf` default :80 server | always, `/healthz` exempt |
| `server.conf.j2` | the Force-SSL :80 server uses the server-rewrite hook instead of the access one |

`$megoopm_crowdsec_appsec` is not rendered on the new server types: it is a
reserved marker the handler does not read (see "Out of scope").

### 3. Data

- Migration `0038`: `crowdsec_enabled BOOLEAN NOT NULL DEFAULT true` on
  `redirection_hosts` and `dead_hosts` (the server default fills existing
  rows, so every existing host is on).
- Models, create schemas (`crowdsec_enabled: bool = True`), update schemas
  (`bool | None = None`), read schemas, `RedirectionHostSpec` /
  `DeadHostSpec` (`crowdsec_enabled: bool = True`), and the loader.
- `openapi.json` and the generated frontend types regenerated.

### 4. UI

The redirection and 404 host dialogs add a "CrowdSec protection" entry to their
existing toggle tables, on by default for a new host, with the description:
"Refuse IPs CrowdSec has banned (and inspect requests with the AppSec WAF).
While CrowdSec is unreachable this host refuses traffic." The default sites
have no control; the Settings → Default site section gains one line saying
banned IPs are refused there.

## Verification before anything else

The bouncer module (lua-cs-bouncer, as vendored in the image) was written for
the access phase. The first implementation task runs it in the server-rewrite
phase against a real CrowdSec 1.6.4 + the MegooPM nginx image and proves:

1. a banned IP gets the configured ban page, with its status;
2. a clean IP passes through to the server's own `return`;
3. AppSec can read a POST body (`ngx.req.read_body`) and blocks a known
   virtual-patching payload;
4. a `captcha` decision serves the captcha page.

If any of these fail in that phase, **stop and report**. The fallback — moving
each `return` into a content handler so the existing access-phase hook runs
first — is a different change and needs its own approval.

## Testing

- Render tests: the hook appears in every redirection/404 server block iff the
  toggle is on; always in default-TLS; the Force-SSL :80 proxy server uses the
  server-rewrite hook; no host renders both hooks.
- A base-config test: the default server carries the hook and exempts
  `/healthz`.
- An `nginx -t` test of every rendered variant in the shipped image.
- Migration test (throwaway-schema harness): existing rows come out `true`;
  downgrade drops the columns.
- API tests: create defaults to `true`, update can switch it off, reads return it.
- Frontend dialog tests: the switch renders, defaults on, and round-trips.

## Out of scope

- **Per-host AppSec.** The vendored module already honours
  `$crowdsec_disable_appsec` per request, contrary to the repo's comments that
  AppSec cannot be switched per host. Wiring the existing
  `crowdsec_appsec_enabled` flag to it is a separate change.
- Switching proxy hosts' :443 servers to the server-rewrite phase.
- Streams (TCP/UDP), which have no bouncer.
