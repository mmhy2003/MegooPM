# CrowdSec protection for streams — design

**Status:** approved 2026-09-06
**Author:** brainstormed with Claude Opus 5

## The problem

The CrowdSec bouncer is attached with `access_by_lua_file` inside the proxy-host
`server {}` blocks. That is an **http-context** directive, and streams render in
nginx's top-level `stream {}` context — `listen` and `proxy_pass`, nothing else.

So an IP that CrowdSec has banned is refused on port 443 and walks straight into
the TCP forward on port 5432. Every stream MegooPM manages is, today,
unprotected by a system the same instance already runs.

## What this is and is not

**Is:** enforcement. A per-stream toggle that drops connections from IPs which
already carry a CrowdSec decision — bans earned by this instance's HTTP traffic,
and the community blocklist.

**Is not:** detection. CrowdSec receives no logs about stream traffic and will
not after this change. Layer 4 carries no application signal: nginx sees a source
IP, a byte count and a duration, never a failed SMTP login or a rejected SQL
credential. A stream can therefore be the *beneficiary* of a ban but never its
cause.

That boundary must be stated in the UI, not just here. An operator who reads the
toggle as "CrowdSec now watches this port" has been misled by us.

## What the probe established

Run against the real `megoopm-nginx` image (OpenResty 1.25.3.2 with
`lua-cs-bouncer` v1.0.8), not against documentation:

1. `preread_by_lua_block` and a stream-context `lua_shared_dict` load and pass
   `openresty -t`.
2. `require("crowdsec")` **succeeds inside the stream Lua VM**, and the module
   exposes `init`, `SetupStream`, `Allow` and `allowIp` there.
3. `csmod.allowIp(ip)` is a pure decision-cache lookup: it takes an IP string,
   reads the shared dict, and returns `(allowed, remediation, err)`. It touches
   no http API — no `ngx.req`, no headers, no response.

Point 3 is what makes this small. The earlier assumption was that the stock
bouncer is unusable outside http and a bespoke handler would be needed; that is
true of `Allow()`, which performs AppSec and writes an HTTP response, and false
of `allowIp()`. We reuse the library through its other entry point.

## Decisions

### A second Lua stack, because VMs cannot share one

`http {}` and `stream {}` are separate Lua VMs in OpenResty. They share no
shared dictionaries and no `init_by_lua` state, so the stream side needs its own:

```nginx
stream {
    lua_package_path "/etc/nginx/lua/?.lua;;";
    lua_shared_dict crowdsec_cache 50m;
    init_by_lua_file /etc/nginx/lua/megoopm_crowdsec_init.lua;
    init_worker_by_lua_block {
        if _G.megoopm_crowdsec and _G.megoopm_crowdsec.SetupStream then
            _G.megoopm_crowdsec.SetupStream()
        end
    }
    include /data/nginx/conf.d/stream/*.conf;
}
```

The init file is **reused unchanged** — it only requires the module, calls
`init()` with the same rendered config, and publishes it on `_G`. Nothing about
it is http-specific.

Two consequences to accept deliberately:

- **A second 50m shared dict per node.** The two caches are independent copies of
  the same decision set. There is no way to share one, and 50m is what the http
  side already reserves.
- **A second LAPI pull.** `SetupStream()` arms its own timer, so each node polls
  LAPI once per interval per context instead of once. That doubles bouncer poll
  traffic, which is small and periodic; the alternative — querying LAPI per
  connection — would put a network round trip in front of every TCP handshake.

### Enforcement is `preread`, and the only remediation is "drop"

`stream {}` has no access phase. The preread phase runs before the connection is
proxied and has `$remote_addr`, which is all the check needs. For UDP it runs on
the first datagram.

A new handler, attached only to streams with the toggle on:

```lua
local csmod = _G.megoopm_crowdsec
if not csmod then
    ngx.log(ngx.ERR, "[megoopm] CrowdSec bouncer not initialised; allowing connection")
    return
end

local ok, allowed = pcall(csmod.allowIp, ngx.var.remote_addr)
if ok and allowed == false then
    return ngx.exit(ngx.ERROR)
end
```

Ban, captcha and throttle all collapse to one action here: there is no page to
serve and no challenge to present at layer 4, so anything that is not "allow"
closes the connection.

`allowIp` returns `true` for *allowed* — confirmed by reading how `Allow()`
consumes it ("if the ip is now allowed"), not inferred. It also returns `true`
when its own configuration is missing or `API_URL` is empty, so the library
fails open by the same rule this handler does.

Calling `allowIp` directly skips one thing `Allow()` does first: the
`is_bouncer_enabled()` check on the config's `ENABLED` key. In MegooPM that key
is hardcoded `ENABLED=true` in `infra/nginx/crowdsec-bouncer.conf` and is not
env-substituted or exposed, so there is nothing to honour. Worth knowing only if
that ever becomes operator-settable, at which point the stream handler must
honour it too.

### Fail open

An uninitialised module, a LAPI outage, or the seconds after a reload before the
first pull lands all mean an empty cache — and an empty cache allows. The window
is bounded by `UPDATE_FREQUENCY=10` in the bouncer config: up to ten seconds
after a reload.

(That config's `MODE=stream` is CrowdSec's own term for "pull decisions
periodically" and has nothing to do with nginx streams. The two meanings sit one
line apart in this feature and will be confused by someone eventually.)

This matches the http handler, which logs and allows when the module is missing.
Failing closed would mean **every nginx reload breaks every stream** until the
first pull completes, and MegooPM reloads nginx on every configuration change it
makes. A CrowdSec outage would take the databases and mail flow behind these
streams down with it. That trade is not worth "no ban ever slips through for
three seconds".

The `pcall` matters for the same reason: an error thrown inside the handler
would abort the connection, turning a bouncer bug into an outage of everything
the toggle is on for.

### The bouncer key is reused, not duplicated

The stream VM registers against LAPI with the same `CROWDSEC_BOUNCER_KEY` and the
same rendered `crowdsec-bouncer.conf`. A separate key would show as a distinct
bouncer in `cscli bouncers list`, which is mildly nicer for metrics, and would
require a new mandatory environment variable on **every node** — a breaking
change for every existing install, in exchange for a nicer listing. Not worth it.

## Storage, API, config

| Piece | Change |
| --- | --- |
| `streams` table | `crowdsec_enabled BOOLEAN NOT NULL DEFAULT false`, migration `0037_stream_crowdsec` (revises `0036_api_keys`) |
| `StreamSpec` | `crowdsec_enabled: bool = False` |
| `stream.conf.j2` | a `crowdsec()` macro emitting `preread_by_lua_file` when the flag is on |
| `loader.py` | copy the column into the spec beside the other stream flags |
| `StreamBase` / `StreamUpdate` | the field, defaulting to `False` |

Off by default. Switching on a security control that silently drops traffic is
never something an upgrade should do on the operator's behalf.

No new routes, so `tests/test_route_authorization.py` is untouched.

## UI

A toggle in the stream dialog, beside the existing options, labelled **CrowdSec
protection**. Its hint carries the boundary:

> Drop connections from IPs CrowdSec has banned — from your HTTP traffic and the
> community blocklist. Streams are forwarded at the TCP level, so CrowdSec cannot
> detect attacks against this port itself.

A shield badge on the streams list marks a protected stream, so the state is
visible while scanning rather than only inside a dialog.

## Testing

Renderer tests, in the style of `tests/test_maintenance_blocks.py`:

- A stream with the toggle off renders no Lua reference at all — the guard must
  be per stream, exactly as it is per host.
- With it on, `preread_by_lua_file` appears inside the `server {}` block.
- It appears for a TCP stream, a UDP stream, and one doing both.
- It appears for a TLS-terminating stream (the certificate branch must not
  displace it).

Plus the migration test (default `false` on an existing row), the API round-trip,
and the frontend dialog/list tests.

**And one verification that is not a unit test.** Fail-open means a broken
bouncer is indistinguishable from "nobody is banned": the silent failure mode.
The rendered config must therefore be run in the real image against a live LAPI,
with an IP banned by `cscli decisions add` and a connection to the stream proved
to be dropped. Text assertions cannot establish this, and without it the feature
can ship doing nothing at all.

## Risks

**The pull timer is the unverified part.** The probe established that
`SetupStream` *exists* in the stream VM. That it *fills the cache* there — the
module's http client working from a stream-context timer — is not yet proven, and
its failure is silent. This is what the live verification above exists to catch,
and it should be done early in implementation rather than at the end.

**Two decision caches can disagree briefly.** The http and stream contexts pull
independently, so for up to one interval a ban is enforced on one and not the
other. Harmless, but worth knowing before someone reports it as a bug.

**A stream can be banned by traffic it never saw.** That is the entire point —
an attacker probing HTTP gets locked out of the database port too — but it also
means a false positive on the HTTP side now costs a stream connection. The
existing whitelist mechanism is the remedy, and it already applies: both contexts
read the same decisions.
