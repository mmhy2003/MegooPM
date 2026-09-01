# Default site — design

## Goal

Let an operator choose what nginx returns for a request that matches no
configured host, from the Settings page: a branded congratulations page, a bare
404, no response at all, a redirect, or one of the Custom Pages authored in the
app. Nginx Proxy Manager offers the same five modes; the difference here is that
Custom HTML picks a page from the Custom Pages library rather than pasting raw
HTML into a settings textarea.

This also introduces the instance-settings store the app has not had until now —
the Settings page is currently a `PagePlaceholder` with no backend behind it.

## Non-goals

- **HTTPS.** The base config declares `default_server` on `:80` only. An
  unmatched *HTTPS* request is currently served by whichever `:443` server block
  nginx loaded first — its certificate and its content. Closing that needs a
  generated self-signed certificate (nothing else can terminate TLS for a name
  that was never configured) plus its regeneration story. NPM has the same gap.
  Explicitly deferred; the include hook this design adds is reusable for it.
- **A CrowdSec ban page.** The other binding for Custom Pages. It sets
  `BAN_TEMPLATE_PATH` in `infra/nginx/crowdsec-bouncer.conf` and is a separate
  change; this design only makes the settings store it will live in.
- **Per-host default pages.** `error_page` on an individual proxy host is a
  different feature with a different data model (a column, not a singleton).
- **A general settings UI framework.** One card for one setting. The store is
  built to hold more, but no settings-registry abstraction is invented for a
  single entry.

## Decisions taken during brainstorming

**HTTP only** (see Non-goals). The operator chose the smallest change that
matches NPM over closing the `:443` SNI hole in the same pass.

**A typed singleton row, not a generic key/value store.** NPM stores settings as
`setting(id TEXT, value TEXT)` holding JSON. That needs no migration per setting,
but it makes the API contract `value: unknown`: validation moves into
hand-written per-key code and the frontend loses generated types. This codebase
is typed end to end — Pydantic schemas, frozen render specs, OpenAPI-generated
TS — so a typed singleton fits the grain. `crowdsec_whitelist_apply` is already
exactly this pattern: one seeded `id=1` row. New settings cost a migration, which
is cheap at 18 revisions in.

**Deleting a Custom Page referenced by the default site is refused**, not
silently unlinked. `proxy_hosts.access_list_id` uses `ON DELETE SET NULL` with
the documented rationale that "attached hosts are simply detached". That
reasoning does not carry here: detaching a guard from one host is visible and
recoverable, whereas silently changing what every unmatched visitor sees is
neither. `ON DELETE RESTRICT`, surfaced as a 409 naming the conflict.

**Seeded as `not_found`, preserving today's behaviour.** The current config
hardcodes `return 404`. Seeding `congratulations` would match NPM's default but
would silently change what a live instance serves the moment the migration runs.
An operator who wants the congratulations page picks it.

**The congratulations page carries the MegooPM theme.** Requested during
brainstorming: same cyberpunk palette as the app, light and dark. Details in
**The congratulations page** below.

## The nginx hook

Today's catch-all, `infra/nginx/nginx.conf`:

```nginx
server {
    listen      80 default_server;
    server_name _;

    location = /healthz { ... }
    location / { return 404; }
}
```

The file is mounted read-only and is not backend-generated, so the backend
cannot rewrite it. A second `location /` alongside an include is an nginx error,
so the hardcoded one is removed rather than kept as a fallback:

```nginx
server {
    listen      80 default_server;
    server_name _;

    location = /healthz { ... }          # unchanged
    include /data/nginx/default/*.conf;
}
```

**With no file present, no location matches and nginx returns 404** — byte for
byte today's behaviour. The fallback is nginx's own semantics, so there is no
window in which a fresh install or a wiped volume serves something unintended,
and `/healthz` never moves, so the container healthcheck is never at risk.

`/data/nginx/default/` is a **sibling** of `conf.d`, not a child: `include
/data/nginx/conf.d/*.conf` is non-recursive but a child directory invites a
future glob change to sweep these `location` fragments into `http {}`, where
they are a syntax error. `data-init` creates it alongside the
`conf.d/stream` it already creates.

## Backend — data model

Migration `0019_instance_settings` creates `instance_settings` and seeds one row
so readers never handle "no row yet", mirroring `crowdsec_whitelist_apply`.

| column | type | notes |
| --- | --- | --- |
| `id` | `Integer` PK, no autoincrement | always `1` |
| `default_site_mode` | enum `default_site_mode` | seeded `not_found` |
| `default_site_redirect_url` | `Text` null | set only for `redirect` |
| `default_site_page_id` | `BigInteger` null | FK → `custom_pages.id`, `ON DELETE RESTRICT` |

`DefaultSiteMode` values: `congratulations`, `not_found`, `no_response`,
`redirect`, `custom_page`. Declared like `AccessListDirective` — a
`values_callable` string enum so the database stores the readable value.

Two CHECK constraints, because a half-configured row renders nginx config that
says nothing:

- `redirect_needs_url`: `default_site_mode <> 'redirect' OR default_site_redirect_url IS NOT NULL`
- `custom_page_needs_page`: `default_site_mode <> 'custom_page' OR default_site_page_id IS NOT NULL`

Pydantic mirrors both as model validators so the API answers 422 with a useful
message instead of surfacing an `IntegrityError`.

### Consequences for Custom Pages

**Delete.** `DELETE /api/v1/custom-pages/{id}` gains an `IntegrityError` branch
returning 409: *"This page is in use by the Default site."* Without it the
RESTRICT constraint surfaces as a 500.

**Edit must now be able to trigger a reload.** The Custom Pages routes
deliberately skip `after_config_write` because "nothing in the rendered
configuration references a page yet". This design is what makes that false. Left
alone, editing the page the default site points at would change the database and
*not* the served page — until some unrelated edit happened to trigger a reload,
at which point the change would appear with no apparent cause. That is worse
than not working.

So `PATCH` and `DELETE` on a custom page look up whether
`instance_settings.default_site_page_id` is this page, and take
`after_config_write` when it is, plain `record_audit` when it is not. Unreferenced
pages keep costing no reload; the referenced one converges immediately. The
module docstring's note to "switch to the shared helper when a binding lands"
is discharged here, conditionally.

A test pins both halves: editing an unreferenced page enqueues zero reloads,
editing the referenced one enqueues exactly one.

## Backend — rendering

A frozen `DefaultSiteSpec` joins `DesiredState`:

```python
@dataclass(frozen=True, slots=True)
class DefaultSiteSpec:
    mode: str
    redirect_url: str = ""
    html: str = ""          # resolved by the loader, not a page id
```

The loader resolves `default_site_page_id` into the page's `html` **before** the
spec is built. The renderer stays a pure function of explicit data with no
database reach-through, which is what makes the whole mode matrix unit-testable
without infrastructure.

`render_default_site(state) -> dict[str, str]` returns the files for
`/data/nginx/default/`:

| mode | `megoopm-default.conf` | `megoopm-default.html` |
| --- | --- | --- |
| `not_found` | `location / { return 404; }` | — |
| `no_response` | `location / { return 444; }` | — |
| `redirect` | `location / { return 301 "<url>"; }` | — |
| `congratulations` | serve-one-file block | the bundled template |
| `custom_page` | serve-one-file block | the page's `html` |

The two HTML modes differ only in file *content*, so they share one block:

```nginx
location / {
    root /data/nginx/default;
    try_files /megoopm-default.html =404;
}
```

`try_files` with an absolute path serves that one document for every URI. The
`.html` extension resolves through `mime.types`, and the response is 200 —
matching NPM's Custom HTML behaviour.

`apply_config` gains a third target beside `confd` and `stream_dir`, using the
list of `(directory, prefix, files)` tuples it already reconciles: one lock, one
`nginx -t`, one rollback covering all three. A bad default site can no more
half-apply than a bad stream can.

`nginx_default_dir` joins `app.core.config.settings`, defaulting to
`{shared_data_dir}/nginx/default`, following `nginx_stream_dir`.

## Security — the redirect URL is config-injection surface

**This is the part of the design that most needs to be right.** The redirect
target is operator input that lands verbatim inside a generated nginx config
file. The engine's `nginx -t` and rollback protect against a config that fails
to *parse*; they do nothing about one that parses fine and does something else.

Validation at the API boundary rejects:

- any scheme but `http` / `https`
- control characters and newlines (a newline ends the directive and starts a new
  one)
- `"`, `'` and `\` (quote-breaking)
- `;` (directive termination)
- `$` — nginx would interpolate a variable into the target. `$request_uri` is a
  plausible thing to want and a deliberate later addition, but a
  reject-by-default posture is the right starting point.

The rendered value is additionally quoted. Both layers stay: validation is the
real defence, quoting is what survives a future validator refactor.

## The congratulations page

A standalone, self-contained HTML document — it is what a visitor sees when
*nothing* is configured, so it must not depend on anything that could be
missing.

**Palette.** Lifted from `frontend/src/app/globals.css`, which is the single
source of truth for the design system:

| role | light — "daylight city" | dark — "neon noir" |
| --- | --- | --- |
| background | `oklch(0.975 0.01 220)` | `oklch(0.15 0.03 285)` |
| foreground | `oklch(0.22 0.05 290)` | `oklch(0.95 0.02 200)` |
| card | `oklch(1 0 0)` | `oklch(0.19 0.035 285)` |
| primary | `oklch(0.50 0.14 205)` | `oklch(0.85 0.16 195)` |
| muted text | `oklch(0.48 0.05 260)` | `oklch(0.72 0.04 215)` |
| accent | `oklch(0.45 0.22 340)` | `oklch(0.88 0.17 340)` |
| border | `oklch(0.86 0.04 210)` | `oklch(0.85 0.16 195 / 18%)` |

Cyan-teal brand, magenta accent, violet-tinted near-black in dark — the same
neon-noir identity as the app.

**Light and dark via `prefers-color-scheme`.** A standalone page has no
`next-themes` and no toggle, so the visitor's OS preference is the only signal
available. Tokens are declared on `:root` for light and overridden in a
`@media (prefers-color-scheme: dark)` block, so the page cannot end up with a
half-applied palette.

**Every oklch value is preceded by a hex fallback.** This page is reachable by
anything that resolves to the proxy, including browsers older than oklch
(Chrome 111 / Safari 15.4 / Firefox 113, 2023). Old browsers take the hex
declaration and ignore the oklch that follows; new ones take the oklch.

**No external requests, ever.** No Google Fonts link, no CDN, no images. The app
self-hosts Inter and JetBrains Mono through `next/font`, which a page served
straight off nginx cannot reach; the font stacks therefore use the system tails
those chains already end in (`ui-sans-serif, system-ui, sans-serif` and
`ui-monospace, SFMono-Regular, Menlo, monospace`). A default site that renders
differently on an air-gapped box, or hangs on a font fetch, defeats its own
purpose.

The cyberpunk treatment is CSS-only — a faint grid wash, a cyan glow on the
heading in dark mode, a magenta rule — kept small and dependency-free. Content
follows NPM's: you have reached MegooPM, this host is not configured, sign in to
the admin panel.

It ships as `backend/app/templates/nginx/congratulations.html.j2` so it loads
through the renderer's existing `FileSystemLoader`.

## Backend — API

`GET /api/v1/settings` and `PATCH /api/v1/settings`, admin-only, both returning
the singleton. No `{id}` — there is one row and the URL should not pretend
otherwise.

These writes go through `after_config_write`: they change rendered config, so
they audit *and* enqueue a reload, returning `X-Config-Reload-Task`.

**Switching mode clears the fields the new mode does not use.** The CHECK
constraints only require that the *relevant* field is present, so a stale
`redirect_url` could linger after a switch to `not_found` — invisible in the
config, but it would reappear in the form if the operator switched back, showing
a URL they thought they had left behind. The service nulls the irrelevant
columns on every mode change, so the stored row always describes exactly one
configuration. A client may therefore `PATCH` `{"default_site_mode": "not_found"}`
alone without having to null anything itself.

## Frontend

`src/app/(app)/settings/page.tsx` stops rendering `PagePlaceholder` and mounts a
new `SettingsView` holding a **Default site** card:

```
┌─ Default site ──────────────────────────────────┐
│ What to serve for a request that matches no      │
│ configured host.                                 │
│                                                  │
│  ○ Congratulations page                          │
│  ● 404 page                                      │
│  ○ No response (444)                             │
│  ○ Redirect                                      │
│  ○ Custom page                                   │
│                                                  │
│                              [ Save changes ]    │
└──────────────────────────────────────────────────┘
```

Selecting **Redirect** reveals a URL field; selecting **Custom page** reveals a
`Select` of the pages from `customPages.list()`.

Needs a new `src/components/ui/radio-group.tsx` wrapping base-ui's `radio` /
`radio-group` (both present in `@base-ui/react`), following the `switch.tsx`
idiom.

**Choosing Custom page with no pages created** shows an empty state linking to
`/custom-pages/new`, rather than an empty dropdown and a save that 422s.

Form state and validation live in a pure `src/components/settings/lib.ts` —
`stateFromSettings`, `buildPayload`, `validateSettingsForm` — so the branching is
unit-testable without mounting the card.

## Testing

**Backend**

- Settings API: the seeded default is readable; each mode round-trips; both
  coherence rules answer 422; a write enqueues exactly one reload; admin-only.
- The redirect-URL validator, one case per rejected class above, plus valid
  `http`/`https` URLs passing.
- `render_default_site` — one test per mode, pure, no database. Asserts the two
  HTML modes emit identical `.conf` text and differ only in the `.html`.
- Deleting a referenced Custom Page → 409; deleting an unreferenced one still
  → 204.
- Editing an unreferenced Custom Page enqueues zero reloads; editing the one the
  default site points at enqueues exactly one.
- Switching mode clears the previous mode's field.
- Migration applied against a fresh database: `upgrade head` from empty, table
  shape, seeded row present, and a `downgrade`/re-`upgrade` round trip. The
  suite builds tables with `create_all` and never exercises migrations.

**Frontend**

- `lib.ts` helpers unit-tested directly.
- `SettingsView`: each radio reveals the right field and hides the others; save
  sends the right payload per mode; the no-pages empty state renders instead of
  an empty select; a load failure offers Retry.

**Not covered by automated tests:** that nginx actually serves each mode. The
render tests pin the emitted text and `nginx -t` gates every apply, but the
end-to-end path is a manual check against a running stack — hit the proxy by IP
with each mode selected.

## Files

**Backend**

- `alembic/versions/0019_instance_settings.py` (new)
- `app/models/instance_settings.py` (new), registered in `models/__init__.py`
- `app/models/enums.py` — `DefaultSiteMode`
- `app/schemas/instance_settings.py` (new)
- `app/services/instance_settings.py` (new)
- `app/api/routes/settings.py` (new), wired in `api/router.py`
- `app/api/routes/custom_pages.py` — 409 on delete-while-referenced
- `app/services/nginx/state.py` — `DefaultSiteSpec`, `DesiredState.default_site`
- `app/services/nginx/loader.py` — resolve the setting and its page
- `app/services/nginx/renderer.py` — `render_default_site`
- `app/services/nginx/engine.py` — third apply target
- `app/core/config.py` — `nginx_default_dir`
- `app/templates/nginx/default_site.conf.j2`, `congratulations.html.j2` (new)
- `tests/test_settings_api.py`, `tests/test_default_site_render.py` (new)

**Frontend**

- `src/components/ui/radio-group.tsx` (new)
- `src/components/settings/{settings-view,lib}.ts[x]` + tests (new)
- `src/app/(app)/settings/page.tsx` — mount the view
- `src/lib/api/resources/settings.ts` (new), exported from `lib/api/index.ts`

**Infra**

- `infra/nginx/nginx.conf` — swap `location /` for the include
- `docker-compose.yml`, `docker-compose.ha.yml` — `data-init` creates
  `/data/nginx/default`

## Open risk

`include /data/nginx/default/*.conf` when the directory does not exist: an nginx
wildcard include matching nothing is not an error, and `data-init` creates the
directory before nginx starts in both compose files. The combination is
nonetheless worth confirming against a running nginx during implementation
rather than trusted from the documentation, because getting it wrong means the
proxy fails to start rather than degrading.
