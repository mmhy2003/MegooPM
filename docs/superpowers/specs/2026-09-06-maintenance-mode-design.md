# Maintenance mode: a host that says "we'll be back"

**Date:** 2026-09-06
**Status:** approved for planning

## The problem

Taking a site down for planned work currently means disabling the proxy host,
which answers with the default site — a "not found" or a congratulations page
that tells a visitor nothing, and tells a search engine the site is gone. There
is no way to say "this is deliberate and temporary", and no way to check your
own deploy before letting the public back in.

## What it does

A proxy host can be marked **under maintenance**. While it is:

- every request to that host is answered with a branded maintenance page,
- the status is **503** with a **`Retry-After`** header, so crawlers keep the
  site's rankings and retry rather than de-indexing it, and uptime monitoring
  reports a real outage instead of a healthy page,
- **addresses on the host's allow-list reach the real site**, so the operator
  can verify a deploy before switching maintenance off.

The page is chosen once, instance-wide, in **Settings → Maintenance page**:
the MegooPM page, or one of your Custom Pages. That mirrors **Ban page**
exactly.

### Why not reuse the branded 503

The instance already ships a branded 503 ("Service unavailable"). Binding
maintenance to it was considered and rejected: "we are doing planned work" and
"an upstream failed" are different messages to a visitor, and conflating them
would make the 503 page unable to say either well. Maintenance gets its own
document and its own copy; it reuses the *mechanism*, not the text.

## Architecture

### The nginx shape, and why it is the only one that works

Measured against `openresty/openresty:1.25.3.2-alpine-fat`, the image this
project runs. Three drafts failed before this one; the failures are recorded
because each looks obviously correct until it is run.

| Attempt | Result |
| --- | --- |
| Server-level `if` → `return 503` | Covers every location — but **bypasses `error_page` entirely**. nginx serves its own built-in body. |
| `rewrite ^ /maint last` to an internal location that returns 503 | Same failure. Once the request has been internally redirected, interception is skipped. |
| `if` **inside each location** + server-level `error_page` | **Works.** The document is served and the allow-list reaches the real site. |
| Maintenance `error_page 503` emitted *after* `include megoopm-errors.conf.inc` | **Silently wrong.** The branded 503 is served instead: at one configuration level, the *first* `error_page` for a code wins. |
| Maintenance `error_page 503` emitted *before* the include | **Works**, and the branded 404 and the rest still work. |

So a host under maintenance renders:

```nginx
# Above the server: which addresses skip maintenance. 0 = let through.
geo $mgm_maint_7 {
    default 1;
    203.0.113.5/32 0;
}

server {
    # BEFORE the errors include: at one level the first error_page for a
    # status wins, so the include would otherwise serve the branded 503.
    error_page 503 /megoopm-maintenance.html;
    location = /megoopm-maintenance.html { root /data/nginx/default; internal; }
    include /data/nginx/default/megoopm-errors.conf.inc;
    ...
    location / {
        if ($mgm_maint_7) { return 503; }
        proxy_pass ...;
    }
    location ^~ /api/ {
        if ($mgm_maint_7) { return 503; }
        proxy_pass ...;
    }
}
```

The guard is repeated in **every** generated location — the root route, each
extra location, the answered locations and the asset-cache location. A
server-level guard would be tidier and does not work. `if` with `return` inside
a location is one of the two uses nginx documents as safe.

**One location is deliberately exempt: the ACME challenge.**
`^~ /.well-known/acme-challenge/` must stay reachable, or a host left under
maintenance fails its certificate renewal — and maintenance lasts hours or
days while renewals are time-sensitive, so planned downtime would quietly
become an expired certificate. The template already carries this exemption for
access lists ("Challenge must stay reachable even when the host is
access-controlled"); maintenance follows it, and a test pins it.

`geo` is an `http`-context directive, and these files are included from
`http{}`, so the block sits above the `server` in the same file. The variable
is named per host id, so two hosts under maintenance cannot collide.

### Data

**`proxy_hosts`** gains:

- `maintenance_enabled` — boolean, default false.
- `maintenance_allow` — `ARRAY(String)` of IPs and CIDRs, default empty. Held
  to the same validation as an access list's client rules.

**`instance_settings`** gains, mirroring the ban page:

- `maintenance_mode` — enum `megoopm` | `custom_page`. No `none`: a host under
  maintenance must answer *something*, and "no page" would mean a bare 503
  that says nothing to a visitor or a crawler.
- `maintenance_page_id` — FK to `custom_pages`, RESTRICT, required when the
  mode is `custom_page`.
- `maintenance_retry_after_minutes` — integer, default 60. Emitted as
  `Retry-After`. Configurable because a fixed guess is wrong for someone, and
  the alternative is editing advanced config.

### Rendering

`render_default_site` writes `megoopm-maintenance.html` into the shared default
directory whenever any host is under maintenance — the shipped template for
`megoopm`, or the referenced Custom Page's HTML. It is written by the same
sweep-by-prefix reconciliation as every other document there, so it disappears
when the last maintenance host is switched off.

The template uses `_palette.css.j2`, the inline base64 logo, and the same
structure as the error pages. Its copy says the site is temporarily down for
planned work and will return — not "service unavailable". Like every other
branded page it **names no host, upstream or path** and **makes no external
request**, because it is reachable by anyone.

`MaintenanceSpec` on the desired state carries `mode`, `html` (already
dereferenced by the loader) and `retry_after_minutes`; `ProxyHostSpec` gains
`maintenance_enabled` and `maintenance_allow`. The renderer stays a pure
function of explicit data, as it is for the ban page.

### API and UI

- The proxy-host schema gains the two fields, with the allow-list validated as
  IPs/CIDRs and rejected otherwise.
- The proxy-host dialog gains a **Maintenance** control in its Advanced tab: a
  switch plus, when on, an allow-list input reusing the existing tag input, and
  a line naming the consequence — every visitor outside the list sees the
  maintenance page.
- Settings gains a **Maintenance page** card: the same two-mode radio and page
  picker as Ban page, plus the Retry-After field.
- The proxy-hosts table shows a badge on a host under maintenance. Without it
  the only signal is inside a dialog, and a host left in maintenance is easy to
  forget.

## Error handling

An allow-list entry that is not a valid IP or CIDR is rejected by the API with
the offending value named. A `custom_page` mode with no page is rejected the
same way the ban page rejects it. A maintenance page whose Custom Page has been
deleted cannot happen: the FK is RESTRICT.

## Testing

- **Renderer**: the `geo` block and its per-host variable name; the guard in
  every location including the asset-cache one; the document written and swept;
  `Retry-After` carrying the configured value.
- **Ordering**, as its own test: the maintenance `error_page` appears before
  `include megoopm-errors.conf.inc`. This is the failure that produces a
  plausible-looking config which silently serves the wrong page.
- **The ACME challenge stays open** under maintenance. Getting this wrong
  expires a certificate days later, far from the change that caused it.
- **Schemas**: the allow-list accepts IPs and CIDRs and rejects anything else;
  `custom_page` without a page is a 422.
- **Migration** against Postgres, including the `ARRAY` column.
- **Frontend**: the dialog shows the allow-list only when the switch is on and
  round-trips both fields; the settings card mirrors the ban-page tests; the
  table badge appears only for a host under maintenance.

## Non-goals

- No scheduled start and end times. Maintenance is switched on and off by hand.
- No per-host page override. One page instance-wide, like the ban page.
- No maintenance for redirection hosts, 404 hosts or streams — a stream is
  layer 4 and has no page to serve.
