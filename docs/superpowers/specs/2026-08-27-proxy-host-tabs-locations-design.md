# Proxy host dialog: tabs and per-path locations — design

Date: 2026-08-27 · Status: approved design, awaiting implementation plan

## Goal

Restructure the new/edit proxy host dialog into **Forwarding / Certificate /
Advanced** tabs, wire up the certificate picker that the data model already
supports but the dialog never exposed, and let one proxy host forward
different URL paths to different upstream pools (the equivalent of NPM's
"custom locations"): each row is `path → upstream pool + scheme`, rendered as
one nginx `location` block.

## Non-goals

- Per-location toggles (websockets, caching, block exploits stay host-wide).
- Regex, exact (`=`) or header-based matchers — plain prefix paths only.
- Per-location `advanced_config`, access lists or CrowdSec settings — those
  are server-level directives and keep covering every location.
- Showing locations in the proxy-hosts table.
- Any change to the `proxy_hosts` columns; the root route stays
  `upstream_id` + `forward_scheme`.

## Decisions taken during brainstorming

- Each Forwarding row carries a **path**; the first row is pinned to `/` and is
  the existing `upstream_id`/`forward_scheme`. Extra rows are new
  `proxy_host_locations` records (approach A: child table with FKs, rather
  than generalising the root into the table or a JSONB column).
- The Certificate dropdown has a **None (HTTP only)** entry. Force SSL, HTTP/2,
  HSTS and HSTS subdomains are disabled while None is selected; their values
  are kept, not reset.
- Domain names, Access list and Enabled stay outside the tabs.

## Backend — data model and API

### Table `proxy_host_locations` (migration `0009_proxy_host_locations`)

| Column | Type | Notes |
|---|---|---|
| `id` | bigint PK | `IdMixin` |
| `proxy_host_id` | FK `proxy_hosts.id` **CASCADE** | indexed |
| `path` | `String(255)` | see rules below |
| `upstream_id` | FK `upstreams.id` **RESTRICT** | indexed; a pool used by a location cannot be deleted |
| `forward_scheme` | enum `http_scheme` (existing type, `create_type=False`) | default `http` |
| `created_at`, `updated_at` | | `TimestampMixin` |

Unique constraint `(proxy_host_id, path)`. Model `ProxyHostLocation` in
`app/models/proxy_host.py`; `ProxyHost.locations` relationship ordered by
`path`, `cascade="all, delete-orphan"`.

### Path rules (pydantic validator on the location schema → 422)

- Starts with `/`.
- Is not exactly `/` (the pinned root row owns that).
- No whitespace and none of `{`, `}`, `;`, `"` (they would escape the
  `location` directive).
- ≤ 255 characters.
- No duplicate paths within one payload (`/api` and `/api/` are distinct, as
  in nginx).

### Schemas (`app/schemas/proxy_host.py`)

- `ProxyHostLocationIn`: `path`, `upstream_id`, `forward_scheme = "http"`.
- `ProxyHostLocationRead(ProxyHostLocationIn)`: adds `id`.
- `ProxyHostBase.locations: list[ProxyHostLocationIn] = []`.
- `ProxyHostUpdate.locations: list[ProxyHostLocationIn] | None = None` —
  `None` leaves the stored list alone, `[]` removes every extra location, a
  list replaces it in full (same contract as upstream backends).
- `ProxyHostRead.locations: list[ProxyHostLocationRead]`.

### Service and routes

- Create/update validate that every `locations[].upstream_id` exists, using
  the same lookup and 404 message the root `upstream_id` uses today.
- Update replaces the location rows in full (delete-orphan handles removals).
- Deleting an upstream referenced by a location hits the RESTRICT FK and
  surfaces through the existing 409 in `app/api/routes/upstreams.py`
  ("still referenced by one or more proxy hosts") — no new code, one test.
- Audit entries for proxy host create/update keep their current shape.
- `backend/openapi.json` is regenerated (`python -m scripts.export_openapi`);
  the frontend's generated types are regenerated with it.

## Backend — nginx rendering

### Spec (`app/services/nginx/state.py`)

`LocationSpec(path: str, upstream_id: int, forward_scheme: str)` and
`ProxyHostSpec.locations: tuple[LocationSpec, ...] = ()`.

### Loader (`app/services/nginx/loader.py`)

- Every location's pool is added to the referenced-upstreams set so its
  `upstream {}` file is emitted.
- A location whose pool is disabled or has no backends is dropped (the rest of
  the host still renders). The existing rule for the root pool — an empty
  root pool skips the whole host — is unchanged.

### Template (`app/templates/nginx/server.conf.j2`)

- `proxy_location(path, scheme, pool, modifier)` replaces the fixed
  `location /` macro; the server blocks call it once for `/` and once per
  `host.locations` row.
- Extra locations render as `location ^~ <path> { … }`. `^~` makes the
  longest matching prefix win over the *Cache assets* regex location, so
  `/api/app.js` reaches the API pool instead of being served (cached) from the
  root pool.
- Host-wide extras apply to every location: `proxy_http_version 1.1`, the
  `X-Forwarded-*` headers, websocket `Upgrade`/`Connection` headers when
  `allow_websocket_upgrade`, and the `Authorization` stripping when the access
  list consumes basic auth.
- Block exploits, HSTS, CrowdSec, access control and `advanced_config` stay
  server-level, unchanged.

## Frontend

### Dialog (`components/proxy-hosts/proxy-host-dialog.tsx`)

```
Domain names  [tags input ────────────────────────────]
Access list   [ None (public) ▾ ]        Enabled  (●  )
┌ Forwarding ┬ Certificate ┬ Advanced ┐
│ (●) Cache assets  (●) Block exploits  (●) Websockets
│ Path     Upstream pool        Scheme
│ /        [ app-pool      ▾ ]  [http ▾]          ← pinned
│ /api/    [ api-pool      ▾ ]  [https▾]   ✕
│ [+ Add location]
└──────────────────────────────────────┘
 error line · Cancel · Save
```

- Outside the tabs: Domain names (full width), Access list, Enabled.
- Tabs use the existing `Tabs/TabsList/TabsTab/TabsPanel` and are
  **controlled**: on a failed submit the dialog switches to the tab that holds
  the offending field before showing the error line.
- **Forwarding:** Cache assets, Block exploits, Websockets toggles, then
  `LocationsEditor` (`components/proxy-hosts/locations-editor.tsx`). Row 0 is
  the root: path rendered read-only as `/`, no remove button; it binds to
  `upstream_id`/`forward_scheme`. Extra rows: path input, pool select, scheme
  select, remove. "+ Add location" appends an empty row.
- **Certificate:** select fed by `certificates.list()`; first entry
  **None (HTTP only)** (`certificate_id: null`). Certificates whose status is
  not `active` are listed with their status and disabled. Below it Force SSL,
  HTTP/2, HSTS, HSTS subdomains — disabled while None is selected, values
  retained.
- **Advanced:** the existing `advanced_config` textarea.
- `certificate_id` is now sent on create and update. CrowdSec flags keep
  passing through untouched.

### View (`components/proxy-hosts/proxy-hosts-view.tsx`)

Loads `certificates.list()` alongside pools and access lists and passes
`certs` to the dialog.

### Helpers (`components/proxy-hosts/lib.ts`, React-free)

- `validateLocations(rows)` → first error `{ message, tab }` or `null`,
  implementing the path rules above plus "row without a pool".
- `stateFromHost(host)` / `buildPayload(form)` moved here from the dialog so
  the form ↔ payload mapping (including `locations` as
  `{path, upstream_id, forward_scheme}`) is unit-testable.

## Error handling

- Client-side validation runs first (as today) so the common mistakes never
  hit the API; server 422s from the path validator and 404s for unknown pools
  surface through the existing `describeError` path.
- A location pointing at a pool that is later emptied simply stops rendering
  (loader rule); nginx config stays valid.

## Testing

### Backend (pytest, Linux container)

- Schema: each path rule rejects with a field error; valid rows pass.
- API: create with locations returns them; update with a list replaces in
  full; `[]` clears; omitted leaves alone; unknown location pool → 404;
  deleting a pool referenced only by a location → 409; deleting the host
  cascades its locations.
- Renderer: extra location block uses `^~`, the row's scheme and pool name;
  websocket headers appear in every location when enabled; cache regex block
  is unchanged; a location with an empty pool is omitted while the host and
  root location render; its pool is not emitted.
- Loader: location pools are included in the referenced set.
- `alembic check` (CI) proves the migration matches the models.

### Frontend (vitest + testing-library)

- `lib.test.ts`: `validateLocations`, `buildPayload` (locations present/absent,
  certificate None → `null`), `stateFromHost` round-trip.
- `proxy-host-dialog.test.tsx`: three tabs render with the expected fields;
  adding a row and saving sends `locations`; selecting None disables the four
  TLS toggles; a bad path switches to the Forwarding tab and shows the error.

## Contract and docs

- `docs/data-model.md`: new table, FK rules row for
  `proxy_host_locations.upstream_id` (RESTRICT) and
  `proxy_host_locations.proxy_host_id` (CASCADE).
- `docs/nginx-engine.md`: short "locations and `^~`" note.
- `backend/openapi.json` + frontend generated types regenerated.

## Files touched

Backend: `app/models/proxy_host.py`,
`alembic/versions/0009_proxy_host_locations.py`, `app/schemas/proxy_host.py`,
`app/services/proxy_host.py` (`create_proxy_host` / `update_proxy_host`),
`app/services/nginx/state.py`, `app/services/nginx/loader.py`,
`app/templates/nginx/server.conf.j2`, `openapi.json`, tests.

Frontend: `components/proxy-hosts/proxy-host-dialog.tsx`,
`components/proxy-hosts/locations-editor.tsx` (new),
`components/proxy-hosts/proxy-hosts-view.tsx`, `components/proxy-hosts/lib.ts`,
`lib/api/generated/schema.ts`, tests.

Docs: `docs/data-model.md`, `docs/nginx-engine.md`.
