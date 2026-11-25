# Static Sites — design

Date: 2026-11-25

## Intent

MegooPM today can forward a domain to a backend (Proxy Hosts), redirect it
(Redirection Hosts) or forward raw TCP/UDP (Streams). The owner also wants to
publish plain HTML5 sites: a folder of `.html`, CSS, images and scripts kept
on the server, served by MegooPM's own nginx without running a web server
for each one.

A **Static Site** is a new item in the sidebar menu, with its own list
and form, next to Proxy Hosts and Redirection Hosts. Each site has its own
domains and settings, and serves one folder.

The owner decided against making this a new location kind inside Proxy
Hosts. A whole domain serving one folder is its own kind of thing, and a
location can never be `/`.

## Goals

- A **Static Sites** page that lists, creates, edits, enables, disables and
  deletes sites. Admins manage them the way they manage Redirection Hosts.
- Each site serves files from one sub-folder of a single **sites folder** on
  the server.
- Error pages:
  - When nginx produces an error for a site (for example a missing page →
    404), it serves the site's own `<code>.html` from the site's folder if
    the file exists, such as `404.html` or `403.html`.
  - Otherwise it serves the MegooPM branded page for that code, the same one
    set in Settings that proxy hosts use.
  - The response keeps the real status code either way.
- It works on a single node and on HA, where every node serves the same files.

## Non-goals

- Uploading, editing or deleting site files from MegooPM. The owner puts the
  files in the folder themself (copy, `git pull`, CI). MegooPM only serves them.
- Single-page app support (React or Vue routing fallback to `index.html`).
  The owner confirmed sites are pure HTML5 pages, so a missing page is always
  a 404.
- Server-side code (PHP, CGI, SSI).
- Backing up site files. MegooPM backups cover its database and `/data`. The
  site files belong to the owner and live outside both.
- Directory listings.

## Anti-goals (a failure even if it works)

- **A site can reach outside the sites folder.**
  - The nginx container also mounts `/data`, which holds certificate private
    keys, CrowdSec credentials and the rendered config.
  - No site setting, file name, `..` path or symlink may let a request read
    anything there.
- **Adding a site needs Docker work.** Editing compose files, restarting
  containers or dropping live connections must not be needed for a new site.
- **A site whose folder is missing or empty breaks nginx** for every other
  host. It must only make that one site answer 404.
- **Publishing files that were never meant to be public** just because they
  sit in the folder, such as a `.git` folder or `.env` after a `git pull`.

## Approaches considered (for getting the server's folder into the containers)

- **A. One sites folder, mounted once (chosen by the owner).**
  - `SITES_PATH` in `.env` names a folder on the server, defaulting to
    `./sites` next to the compose file, like `BACKUP_PATH`.
  - It is mounted read-only into nginx, and into the backend and worker so
    the form can list sub-folders.
  - Each site picks a sub-folder. A new site is UI-only work.
  - On HA, `SITES_PATH` points at the shared mount.
- **B. Any server path per site.** Rejected:
  - a running container cannot gain mounts, so every new site would mean
    editing compose and recreating nginx;
  - a free-form path is the exposure risk in the anti-goals.
- **C. Under the existing `/data` volume (`/data/sites`).** Rejected:
  - on a single node `/data` is a named Docker volume, so updating files
    means `docker cp` or digging up the volume path;
  - it puts sites next to the certificate keys.

## Decided by the owner

- Static Sites is its own sidebar menu item and page, not a proxy-host
  location kind.
- The folder lives on the server and is mounted into the containers
  (approach A).
- Sites are plain HTML5, so there is no single-page app mode.
- Error handling: the site's own `<code>.html` first, then MegooPM's
  branded page.
- Files are copied straight into the folder, and all symlinks are blocked.

## Design

### The sites folder

- Compose (`docker-compose.yml`, `docker-compose.dev.yml`,
  `docker-compose.ha.yml`) mounts `${SITES_PATH:-./sites}` at `/sites`, read-only.
  - It goes into the `nginx` service and into the services that validate and
    list folders (`backend`; also `worker` if rendering needs it).
  - On HA the default is not useful: `.env.ha.example` and `docs/ha.md` say to
    set `SITES_PATH` to a folder on the shared mount.
- `data-init` does not create, chown or write to it. MegooPM never writes there.
- **File permissions.** The files must be readable by everyone (files `o+r`,
  folders `o+rx`).
  - nginx workers run as `nobody`, because infra/nginx/nginx.conf has no
    `user` directive. The backend runs as uid 1000 (`appuser`).
  - Files owned by whoever copied them in are fine as long as they are
    world-readable. The docs say so.
  - A folder the backend can't read is reported by the folders endpoint as
    unreadable, not silently left out.
- Document it in the env examples, the README/deploy docs and `docs/ha.md`.

### The data model

A new table, `static_sites`. Its fields mirror `redirection_hosts`
([backend/app/models/redirection_host.py](../../../backend/app/models/redirection_host.py)):

- `domain_names`
- `certificate_id`
- `ssl_forced`
- `http2_support`
- `hsts_enabled` and `hsts_subdomains`
- `block_exploits`
- `crowdsec_enabled` (default true, like redirection hosts)
- `advanced_config`
- `enabled`
- timestamps

On top of that, it has:

- `folder`: the site's sub-folder, relative to `/sites`. Examples:
  `company-site`, `clients/acme`.
  - Rules: one or more segments joined by `/`. Each segment uses letters,
    digits, `.`, `_` and `-`. A segment may not start with `.`, so no `..`
    and no hidden folders. No leading or trailing `/`. At most 255 characters.
  - It is validated in the API schema and backed by a DB check constraint
    where practical.
- `access_list_id`: optional, like proxy hosts, because an internal docs site
  is a likely use.
  - It uses the same access-list behaviour as proxy hosts: basic auth, client
    rules and satisfy any.
  - It is RESTRICT on delete, like the other foreign keys.

Domain uniqueness:

- A domain may belong to only one of proxy hosts, redirection hosts, dead
  hosts or static sites.
- Use the cross-table check those already use and extend it to the new table.

### The API

`/api/static-sites` offers list, get, create, update, delete, and
enable/disable. It has the same shape, auth (admin) and audit logging as
`/api/redirection-hosts`.

It also offers `GET /api/static-sites/folders`:

- It lists the sub-folders under `/sites`, up to a sensible depth such as 2,
  skipping hidden ones.
- For each folder it gives a flag for whether `index.html` exists.
- When `/sites` isn't mounted or is empty, it returns an empty list plus a
  reason the UI can show.

Saving a site whose folder doesn't exist (yet) or has no `index.html` is
**allowed** but returns a warning in the response. The owner may create the
site first and copy files in later. The UI shows the warning.

### nginx

The site gets a new template `static_site.conf.j2`, rendered and applied
through the same pipeline as the other host kinds: loader, state spec,
renderer, apply, and HA config sync.

The server blocks are structured like `redirect.conf.j2`:

- a `:80` block;
- a `:443` block when a certificate is attached;
- `ssl_forced` makes `:80` bounce to HTTPS;
- the ACME challenge location;
- HSTS, the block-exploits checks, the CrowdSec bouncer and the live-log tag;
- `advanced_config`.

The site-specific parts:

- **Document root.**
  - `root /sites/<folder>;`, `index index.html;`, `autoindex off;`.
  - `try_files $uri $uri/ =404;`.
- **Symlinks.** `disable_symlinks on;`. Every symlink is blocked, including
  a symlinked site folder, so a link planted in a site cannot point nginx at
  `/data`.
  - The owner confirmed they copy files straight into the folder (scp, rsync,
    `git pull`) and don't use release-switching symlinks.
  - The docs say symlinks are not served.
- **Hidden files.**
  - Any path segment starting with `.` answers 404. This covers `.git`,
    `.env` and `.htaccess`.
  - The one exception is `/.well-known/`, which a site may legitimately
    publish. The ACME challenge location still takes precedence for
    `/.well-known/acme-challenge/`.
- **Error pages.** For each code in `ERROR_CODES`
  ([backend/app/models/error_page.py:20](../../../backend/app/models/error_page.py#L20):
  400, 401, 403, 404, 500, 502, 503, 504):
  - `error_page <code>` goes to a named location;
  - that location tries `/<code>.html` in the site's root first, then falls
    back to the branded MegooPM document that `errors.conf.inc.j2` maps today;
  - the status code is preserved.

  Suggested shape:

  ```nginx
  error_page 404 @site_404;
  location @site_404 {
      root /sites/<folder>;
      try_files /404.html /<branded-404-document>;   # last arg is the branded page's URI
  }
  ```

  The branded documents live under `nginx_default_dir`, not under the site
  root, so the last `try_files` argument can't simply be a path under the
  site root.
  - The intended shape is a named location that tries the site's file, then
    falls back to an internal location rooted at the default dir. That is the
    same one `errors.conf.inc.j2` generates today, so it can be reused or
    adapted.
  - The real status code is kept in both cases.

  The builder picks the exact wiring that keeps the status code and the
  branded fallback. The sites' own error files are served from the site's
  root, so `disable_symlinks` and the hidden-file rule apply to them too.
- **Missing folder.** If `/sites/<folder>` doesn't exist, nginx still
  loads: a missing `root` is not a config error. Requests then get the
  branded 404. A test should pin this.
- **Disabled sites.** They are handled the same way disabled redirection
  hosts are, whatever that pipeline already does.

### Live logs, dashboard, analytics

- **Live logs.**
  - Static sites get their own host key prefix, for example `w<id>`. It must
    not collide with p/r/d/s/u/n/all. `parse_host_key`, the relay, the
    `_live_log.j2` tag and the host picker must all accept it.
  - Static sites show in the Live logs host picker on the Requests and Errors
    tabs, grouped like the other kinds.
- **Dashboard.** Its summary counts static sites next to the other host kinds.
- **Analytics.** Static sites use the same visitor-analytics log path as the
  other hosts.

### Frontend

- **Navigation.** A **Static Sites** nav item in
  [frontend/src/config/nav.ts](../../../frontend/src/config/nav.ts), placed
  after Redirection Hosts, with a fitting lucide icon.
- **Page.** `/static-sites`, built from the Redirection Hosts page and dialog
  (`frontend/src/components/redirection-hosts/`):
  - a list showing domains, folder, certificate/SSL and status;
  - the same row actions.
- **Dialog fields.**
  - Domains.
  - **Folder**: a searchable dropdown fed by `/api/static-sites/folders`. Each
    entry shows "no index.html" when that applies. It also lets the admin
    type a folder that doesn't exist yet, which is saved with the warning.
  - A short help line saying where the sites folder is on the server and
    that each site is one sub-folder.
  - The page hint: add `404.html` (or `403.html` and so on) to the folder to
    replace MegooPM's error page.
  - Access list.
  - The SSL tab, the same as for redirection hosts.
  - CrowdSec.
  - Advanced.
- **Empty state.** When `/sites` isn't mounted, the page explains how to set
  `SITES_PATH` instead of showing an empty dropdown.

### Tests

They follow the existing patterns:

- **Schema.** Folder validation, rejecting `..`, absolute paths, hidden
  segments, empty segments and bad characters.
- **API.** CRUD, auth, audit, domain conflicts with other host kinds, and the
  folder warnings.
- **Folders endpoint.** It runs against a temp directory.
- **Renderer snapshots.**
  - Variants: plain, TLS, forced SSL, and with an access list.
  - The error-page fallback.
  - The hidden-file rule and `disable_symlinks`.
- **Folder escape.** A rendered config with a folder value attempting escape
  is impossible, because the schema rejects it. A test proves that.
- **Harness check.** If one exists for templates, `nginx -t` on the rendered
  output, including a site whose folder is missing.
- **Frontend.** vitest for the view and dialog, following
  `redirection-hosts-view.test.tsx` and `redirection-host-dialog.test.tsx`.

### Trust and integration points

- **`advanced_config` is trusted admin input**, exactly as it is for proxy
  and redirection hosts.
  - The anti-goal covers what a *visitor* can reach through requests, file
    names and folder values. An admin who writes `root /data;` in advanced
    config has the same power they already have on a proxy host.
  - No new directive filter. (My call.)
- **The folders endpoint and the save warning are advisory**, read from the
  local node's `/sites`. Nothing blocks on them, so lag on an HA shared
  mount only affects the warning.
- **Integration points.** Grep `RedirectionHost` / `redirection_host` and
  extend each place that lists host kinds. Expected places:
  - certificate in-use and delete blocking;
  - access-list in-use and delete blocking, which is new for this kind;
  - domain conflict checks;
  - backup/export and restore of DB rows;
  - the nginx loader and state;
  - the dashboard summary;
  - the live-logs host list;
  - the audit log entity types.

## Constraints

- Follow the existing host-kind patterns. Don't invent new infrastructure.
- An Alembic migration comes next after the current head.
- The sites folder is mounted read-only everywhere, and MegooPM never
  writes to it.
- Commits end with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Left to the builder

- The exact nginx wiring for the error fallback, as long as it keeps the
  status and serves the branded page.
- The host-key letter for live logs.
- The nav icon.
- The folder depth listed by the folders endpoint.
- How the folder dropdown accepts a typed, not-yet-existing folder.

## My calls (not yet confirmed by the owner)

- **Access lists on static sites**, which redirection hosts don't have.
- **`advanced_config` is trusted.** No new filter on nginx directives.
- **Files must be world-readable.** nginx reads them as `nobody`.
- **Dotfiles answer 404.** Every path segment starting with `.` is hidden,
  except `/.well-known/`.
- **A missing folder or no `index.html` warns but doesn't block saving.**
- **The default `SITES_PATH` is `./sites`.**
- **CrowdSec is on by default**, like redirection hosts.
- **Plain paths only.** `/about` serves `about/index.html` (via the usual
  `/about` → `/about/` redirect). It does not serve `about.html`.
