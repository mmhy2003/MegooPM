# Branded HTTP error pages — design

## Goal

A visitor who hits an error on any domain MegooPM serves sees a page that
looks like MegooPM: the product palette, the neon dark treatment, the logo. An
operator can replace any one of them with a custom page they designed.

The two pages that already exist — the default site's "you've reached the
proxy" and the CrowdSec ban page — are brought to the same standard, logo
included.

## Decisions taken with the user

- **Only errors nginx itself produces.** No `proxy_intercept_errors`: an
  application that renders its own error pages keeps them, and an API behind
  the proxy still returns its JSON error bodies.
- **Eight codes:** 400, 401, 403, 404, 500, 502, 503, 504.
- **Every managed server block:** proxy hosts, redirection hosts, 404 hosts,
  and the catch-all default server.

## What the code already does

- `render_default_site(state)` writes the shared default directory:
  `megoopm-default.conf` (a bare `location` the base config includes inside
  its `default_server`), `megoopm-default.conf.body`, `megoopm-default.html`,
  `megoopm-ban.html`, and one `megoopm-location-<id>.html` per custom-page
  location. `apply_state` reconciles that directory by managed prefix, so
  files that stop being rendered are swept.
- `DefaultSiteSpec` and `BanPageSpec` carry a `mode` plus an `html` the loader
  has already dereferenced, keeping the renderer a pure function of explicit
  data. A missing page renders an empty document rather than dropping the
  whole config.
- `congratulations.html.j2` and `banned.html.j2` share one palette, lifted
  from `frontend/src/app/globals.css`, with a hex fallback before every
  `oklch()` and a `prefers-color-scheme: dark` block ("neon noir"). Both
  state, in comments, that they make **no external requests at all**.
- Neither carries the logo: both use a text wordmark, `<p class="wordmark">`.
- `custom_pages` holds named HTML documents; `instance_settings` references
  one for the default site and one for the ban page, both FK RESTRICT.
- The four server templates each render one or more `server {}` blocks and
  already include shared fragments through Jinja macros.

## Storage

A new table, `error_page`, one row per **configured** code:

| column | note |
| --- | --- |
| `code` (PK) | smallint, one of the eight |
| `mode` | enum `default`, `custom_page` |
| `custom_page_id` | FK `custom_pages` RESTRICT, null unless the mode is `custom_page` |
| `created_at`, `updated_at` | as every table |

A code with **no row** means the branded default. A fresh install therefore
needs no seed, and the table holds only what an operator changed. Setting a
code back to the default deletes its row rather than storing a redundant one.

A check constraint mirrors the shape: `custom_page_id` is not null exactly
when the mode is `custom_page`.

Migration `0032_error_pages`.

## Rendering

**The documents.** `render_default_site` gains one file per code,
`megoopm-error-<code>.html`, always written for all eight — either the
rendered template or the operator's chosen document. Always writing them
means a server block's `error_page` directive can never point at a file that
does not exist.

**The wiring.** One new fragment, `megoopm-errors.conf.inc`, written to the
same directory and containing, for each code:

```
error_page 404 /megoopm-error-404.html;
location = /megoopm-error-404.html {
    root /data/nginx/default;
    internal;
}
```

`internal` means the file is unreachable by a direct request: it is served
only as the result of an error inside nginx. The extension is `.inc`, not
`.conf`, so the base config's `include .../*.conf` never parses it as a
top-level file.

Each of the four templates gains one line inside every `server {}` block:

```
include /data/nginx/default/megoopm-errors.conf.inc;
```

A single line per block rather than eight directives, so the templates stay
readable and the set of codes lives in one place.

**Precedence.** `error_page` is inherited by locations that do not override
it. A proxy host's `location /` proxies onward, so an error the *backend*
returns passes through untouched — the branded page appears only when nginx
generates the status itself. This is exactly the decision above, and it falls
out of not setting `proxy_intercept_errors` rather than needing extra config.

**Specs.** `ErrorPageSpec(code, html)` — the document, already dereferenced by
the loader, exactly as `BanPageSpec` works. `DesiredState` gains
`error_pages: tuple[ErrorPageSpec, ...]`. When a custom page has gone missing
(the row was edited outside the API), the loader falls back to the branded
template rather than writing an empty document, because an empty error page is
worse than a generic one.

## The pages

One template, `error.html.j2`, rendered eight times with the code and its
copy. Per-code copy, chosen so each says something true and useful without
describing the instance:

| code | heading | sentence |
| --- | --- | --- |
| 400 | Bad request | The server couldn't understand that request. |
| 401 | Authentication required | This area needs credentials. |
| 403 | Access denied | You don't have permission to view this. |
| 404 | Not found | There's nothing at this address. |
| 500 | Something went wrong | The site hit an unexpected error. |
| 502 | Bad gateway | The site behind this address didn't respond correctly. |
| 503 | Service unavailable | The site is temporarily unable to handle the request. |
| 504 | Gateway timeout | The site behind this address took too long to answer. |

The code is shown large, the heading below it, the sentence below that. No
request data, no host name, no upstream address: an error page is reachable by
anyone, and naming the backend tells a prober how the instance is built. Same
rule the ban page already follows.

**The logo.** A small PNG, generated once from `frontend/public/logo.png` and
committed at `backend/app/templates/nginx/assets/logo.png`, embedded as a
base64 `data:` URI by a Jinja global. Not a linked file: these pages must
render when nothing else works, which is precisely when a second request is
least likely to succeed, and the existing templates already promise no
external requests. Sized to keep each page well under 30 KB.

`congratulations.html.j2` and `banned.html.j2` swap their text wordmark for
the same logo, and adopt the shared palette block so all three files stay in
step. The palette moves into `error_palette.css.j2`, included by all three,
so a colour is changed once rather than three times.

## Settings

A new card, **Error pages**, below the existing ones. Eight rows, each with
the code and its name, a two-option select (MegooPM page / Custom page), and
a page picker that appears only for the second. One Save for the card,
disabled until something changes, matching the ban-page card exactly.

The card is hidden behind nothing: unlike passkeys or the blocklist, error
pages need no other setting to work.

## API

- `GET /settings/error-pages` → `list[ErrorPageRead]`, one entry per code,
  always all eight, with the effective mode. A code with no row reports
  `default`.
- `PUT /settings/error-pages` → the whole set, `list[ErrorPageUpdate]`. A
  whole-set write rather than eight patches: the card saves once, and a
  partial write would leave the operator guessing which rows took effect.
  Rows with mode `default` delete any stored row. 422 when a mode is
  `custom_page` without a page, or when the page does not exist. Admin-only,
  audited, and followed by `after_config_write` so nginx re-renders.

## Error handling

| situation | result |
| --- | --- |
| a code with no row | branded default; no storage written |
| `custom_page` without a page | 422 from the schema, naming the code |
| page id does not exist | 422 from the service, naming the code |
| page deleted while bound | refused by the FK (RESTRICT), as the default site already is |
| page missing at render time | branded template, never an empty document |
| a code not in the eight | 422; the set is fixed |

## Testing

**Backend**

- `test_error_pages_render.py` — all eight files written with no rows; a
  custom page replaces exactly one; the fragment lists all eight codes and
  marks each location `internal`; the fragment's extension is not `.conf`; a
  missing document falls back to the branded template.
- Each of the four server templates includes the fragment in **every**
  `server {}` block it renders (a TLS host renders two).
- `test_error_pages_templates.py` — every rendered page embeds the logo as a
  `data:` URI, contains no `http://` or `https://` reference, names its own
  code, and never names a host or upstream. The same assertions run against
  the congratulations and ban pages.
- `test_error_pages_api.py` — the read returns eight rows; a whole-set write
  stores only the non-default ones; setting a row back to default deletes it;
  the two 422s; admin-only; the audit row.
- `test_error_pages_migration.py` — the table and its constraint, in the
  style of `0031`'s test.

**Frontend**

- `error-pages-card.test.tsx` — eight rows; the page picker appears only for
  the custom mode; Save is disabled until a change; the payload carries all
  eight rows; a load failure shows the error rather than an empty card.

## Files

**Backend**

- `app/models/enums.py` — `ErrorPageMode`
- `app/models/error_page.py` (new), `app/models/__init__.py`,
  `alembic/versions/0032_error_pages.py`, `tests/conftest.py`
- `app/services/nginx/state.py` — `ErrorPageSpec`, `DesiredState.error_pages`
- `app/services/nginx/loader.py` — load the rows, dereference the documents
- `app/services/nginx/renderer.py` — the eight documents and the fragment
- `app/templates/nginx/error.html.j2`, `error_palette.css.j2`,
  `errors.conf.inc.j2`, `assets/logo.png` (new)
- `app/templates/nginx/congratulations.html.j2`, `banned.html.j2` — logo and
  the shared palette
- `app/templates/nginx/server.conf.j2`, `redirect.conf.j2`, `dead.conf.j2`,
  `default_tls.conf.j2` — the include line
- `app/schemas/error_page.py` (new), `app/services/error_page.py` (new),
  `app/api/routes/settings.py`
- `backend/openapi.json`

**Frontend**

- `src/lib/api/resources/settings.ts` — the two calls and their types
- `src/components/settings/error-pages-card.tsx` (new),
  `settings-view.tsx` — mount

## Non-goals

- **Intercepting backend error responses.** Decided above; it would hide
  application error pages and break JSON APIs.
- **Per-host error pages.** Instance-wide only. A host that needs its own can
  use a custom-page location, which the previous project added.
- **Editing the branded templates from the UI.** An operator who wants
  different words designs a custom page; the shipped ones stay shipped.
- **Codes beyond the eight**, including a 4xx/5xx catch-all.
- **Stream (TCP/UDP) blocks.** There is no HTTP status to answer with.

## Open risks

- **Page weight.** Eight documents plus the logo live in the shared default
  directory and are written on every apply. At roughly 20 KB each this is
  under a quarter megabyte, but the logo's size is the whole budget, so the
  generation step pins its dimensions rather than copying the 512px original.
- **`error_page` inheritance.** A location that sets its own `error_page`, or
  one added later by advanced config, overrides the inherited directives. The
  advanced-config field is free text, so an operator can defeat this
  deliberately; nothing here tries to stop them.
- **A custom page that is itself broken.** MegooPM serves whatever document
  the operator stored, exactly as it does for the default site. A page that
  references an external stylesheet will look wrong precisely when the network
  is failing, which is the case the branded defaults are built for.
