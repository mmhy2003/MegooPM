# Static Sites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Static Sites" host kind that serves plain HTML from a sub-folder of one read-only `/sites` mount, configured like a redirection host.

**Architecture:** A new `static_sites` table, service, and admin API, cloned from redirection hosts. The nginx loader and renderer turn each enabled row into a `server {}` block from a new `static_site.conf.j2`. That block serves `root /sites/<folder>`, blocks hidden paths and symlinks, and falls back from the site's own error pages to MegooPM's branded ones. The frontend gets a `/static-sites` page cloned from the redirection-hosts components, with a folder picker fed by a `GET /static-sites/folders` endpoint.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, Pydantic v2, Jinja2 nginx templates, OpenResty; Next.js 16 / React 19 / base-ui, vitest.

**Spec:** `docs/superpowers/specs/2026-11-25-static-sites-design.md`

## Global Constraints

- Follow the redirection-host pattern file for file (model, schema, service, route, spec, loader, renderer, tests). Reach for something new only when the spec asks for it.
- The mount is `${SITES_PATH:-./sites}:/sites:ro` on nginx, backend and worker in `docker-compose.yml`, `docker-compose.dev.yml` and `docker-compose.ha.yml`. `data-init` never touches it. MegooPM never writes to `/sites`.
- The in-container root is `settings.sites_root`, default `"/sites"`.
- Folder value: one or more `/`-joined segments.
  - Each segment matches `[A-Za-z0-9._-]+` and doesn't start with `.`.
  - No leading or trailing `/`, no empty segment, at most 255 chars.
- Served paths:
  - `disable_symlinks on;` blocks every symlink, including a symlinked site folder.
  - Any path with a segment that starts with `.` returns 404, except `/.well-known/`.
  - `autoindex off`. `/about` serves `about/index.html`. There is no `about.html` fallback and no SPA mode.
- Error pages, for every code in `ERROR_CODES` (`app/models/error_page.py`):
  - the site's own `/<code>.html` first, then MegooPM's branded document;
  - the real status code is kept.
- `crowdsec_enabled` defaults to true. `access_list_id` is optional. Both the certificate and access-list FKs use `ondelete="SET NULL"`, the same as proxy hosts.
- Folder warnings (missing folder, no `index.html`, unreadable, not mounted, symlink) are advisory and never block a save.
- `advanced_config` is trusted admin input. No directive filter.
- Live-log host key prefix: `w`, so `w<id>`.
- Alembic revision `0042_static_sites`, down revision `0041_waf_body_limit`.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Backend tests run in the Linux test container. There is no `fcntl` on Windows, so never run pytest natively.
- Format only the files you changed: `ruff format <files>` and `ruff check <files>`. Never run prettier over the frontend tree.
- Frontend: 41 tests fail before this work, in `login-form.test.tsx` and `recent-accounts.test.ts`. They are not regressions. Every other test must stay green, and `npx tsc --noEmit` and `npx eslint <changed files>` must be clean.

## Recon facts (decided, not up to the builder)

- There is no cross-table domain-conflict check anywhere. Proxy and redirection routes don't check either. The spec's "existing cross-table check" doesn't exist, so this plan adds none. (Ruling — cost if wrong: one follow-up validator.)
- Backup is a whole-database `pg_dump`, so it needs no change.
- Audit `object_type` is free text: `"static_site"`.
- Access-list delete has no in-use guard for proxy hosts (FK SET NULL). Static sites match it, so there is no new guard. (Ruling — matches existing behavior; cost if wrong: one guard.)
- htpasswd files are keyed by access-list id (`htpasswd_filename(access_list.id)` in `app/services/nginx/renderer.py:97`). A static site reuses the same file name, so writing it twice in the `files` dict is harmless.
- An `error_page` directive for a code at one level: the first one wins. The site's `error_page <code> @site_<code>;` lines must come **before** `include {{ default_dir }}/{{ errors_conf }};` in each server block.

## Review Focus

1. **Folder escape.** A folder of `../data`, `/data`, `a/../../x`, `.git` or `a//b`, or a URL-encoded `%2e%2e`, must never reach `root`. The schema rejects these, the DB CHECK rejects them, and `folder_status` refuses any resolved path outside `sites_root`. Tests are in Task 1 and Task 2.
2. **`/sites` not mounted, or empty.**
   - The folders endpoint returns `available: false` with a reason, not a 500.
   - Saving still works and reports `folder_status: "unmounted"`.
   - nginx still passes `nginx -t`.
   - Tests are in Task 2 and Task 3.
3. **Error page keeps the status.** A missing page answers 404, never 200, whether the site has `404.html` or not. A `.git/config` request answers 404. A symlink answers 403. Pinned by the runtime harness in Task 4.
4. **The ACME challenge still works with an access list and with the hidden-path rule.** `/.well-known/acme-challenge/x` is served from the challenge dir even when an access list exists. Render test in Task 3, harness check in Task 4.
5. **A disabled site, or a site whose certificate was deleted,** renders no block or an HTTP-only block. It doesn't render a broken `ssl_certificate` line. Render test in Task 3.

---

### Task 1: Model, migration, schema, setting

**Files:**
- Create: `backend/app/models/static_site.py`
- Create: `backend/alembic/versions/0042_static_sites.py`
- Create: `backend/app/schemas/static_site.py`
- Modify: `backend/app/models/__init__.py` (register `StaticSite`)
- Modify: `backend/app/core/config.py` (add `sites_root: str = "/sites"`)
- Test: `backend/tests/test_static_sites_schema.py`
- Test: `backend/tests/test_static_sites_migration.py` (follow `tests/test_crowdsec_more_hosts_migration.py`)

**Interfaces:**
- Produces:
  - `StaticSite` ORM model. Table `static_sites`. Columns are the redirection-host columns minus the `forward_*`/`preserve_path` ones, plus:
    - `folder: Mapped[str]` (String(255), not null);
    - `access_list_id: Mapped[int | None]` (FK `access_lists.id`, `ondelete="SET NULL"`);
    - relationships `certificate` and `access_list`.
  - `FOLDER_RE: re.Pattern[str]` in `app/schemas/static_site.py`. It matches a whole valid folder value.
  - Schemas `StaticSiteCreate`, `StaticSiteUpdate` (all fields optional), and `StaticSiteRead`.
  - `StaticSiteRead` has every column plus `folder_status: FolderStatus`. Task 2 fills it in.
  - `FolderStatus = Literal["ok", "no_index", "missing", "unreadable", "symlink", "unmounted"]`, defined in `app/schemas/static_site.py`.

- [ ] **Step 1: Write the failing schema tests**

```python
@pytest.mark.parametrize("folder", ["company-site", "clients/acme", "a.b_c-1", "x/y"])
def test_folder_accepts(folder):
    assert StaticSiteCreate(domain_names=["a.example.com"], folder=folder).folder == folder

@pytest.mark.parametrize("folder", ["", "/abs", "trail/", "a//b", "..", "a/../b", "../data",
                                    ".git", "a/.hidden", "sp ace", "a\\b", "%2e%2e", "x" * 256])
def test_folder_rejects(folder):
    with pytest.raises(ValidationError):
        StaticSiteCreate(domain_names=["a.example.com"], folder=folder)

def test_update_validates_folder_too():
    with pytest.raises(ValidationError):
        StaticSiteUpdate(folder="../x")

def test_crowdsec_defaults_on():
    assert StaticSiteCreate(domain_names=["a.example.com"], folder="s").crowdsec_enabled is True
```

The domains go through `_normalise_domains` from `app.schemas.proxy_host`, the same as `RedirectionHostCreate`. Copy one domain-normalising test from the redirection schema tests.

- [ ] **Step 2: Run it; expect a FAIL on import (`app.schemas.static_site` missing)**

Run: `pytest tests/test_static_sites_schema.py -q`

- [ ] **Step 3: Implement the model, the schemas, `FOLDER_RE` and the setting**

`FOLDER_RE` is `^(?!\.)[A-Za-z0-9_-][A-Za-z0-9._-]*(/(?!\.)[A-Za-z0-9_-][A-Za-z0-9._-]*)*$`. The length is checked separately: at most 255.

- [ ] **Step 4: Write migration `0042_static_sites`**

Create `static_sites`. Copy the redirection_hosts column definitions from `0003_core_domain_core_domain_model.py`, including the `crowdsec_enabled` server_default `true` (see `0038`). Add the two FKs with `SET NULL`, and an index on `enabled`.

Add a CHECK constraint `ck_static_sites_folder`. It uses Postgres regex `folder ~ '<FOLDER_RE>' AND length(folder) <= 255`. Write the pattern once in the migration; it can't import the app.

The downgrade drops the table.

The migration test does three things:
- upgrades to head;
- shows that inserting `folder='../x'` raises IntegrityError;
- downgrades to `0041_waf_body_limit`.

- [ ] **Step 5: Run the schema tests and the migration test; expect PASS**

Run: `pytest tests/test_static_sites_schema.py tests/test_static_sites_migration.py -q`

- [ ] **Step 6: Commit** — `feat(static-sites): model, migration and schema`

---

### Task 2: Service, folder status, API routes

**Files:**
- Create: `backend/app/services/static_site.py`
- Create: `backend/app/api/routes/static_sites.py`
- Modify: `backend/app/api/router.py` (`include_router(static_sites.router, prefix="/static-sites")`, after redirection-hosts)
- Test: `backend/tests/test_static_sites_api.py` (fixtures as in `tests/test_meg24_api.py`)
- Test: `backend/tests/test_static_sites_folders.py` (pure, uses `tmp_path`)

**Interfaces:**
- Consumes: Task 1 models and schemas.
- Produces:
  - `folder_status(root: Path, folder: str) -> FolderStatus`. Return values:
    - `"unmounted"`: the root is missing or has no entries;
    - `"symlink"`: any component of the site path is a symlink;
    - `"missing"`;
    - `"unreadable"`: the folder can't be opened or listed;
    - `"no_index"`;
    - `"ok"`.

    If the resolved path isn't inside the resolved root, it returns `"missing"` and never touches the path outside.
  - `list_folders(root: Path, max_depth: int = 2) -> FoldersResult`.
    - The result is a Pydantic model `{available: bool, reason: str | None, folders: list[FolderEntry]}`, where `FolderEntry = {path: str, has_index: bool, readable: bool}`.
    - It skips hidden entries and symlinks, sorts by path, and stops below depth 2.
    - `reason` is `"not_mounted"` when the root is missing, or `"empty"` when the root has no visible folders.
  - Service functions `list_static_sites`, `get_static_site`, `create_static_site`, `update_static_site`, `delete_static_site`, with the same signatures as `app/services/redirection_host.py`. They raise `StaticSiteNotFound` and `InvalidReferenceError`.
  - Routes under `/api/v1/static-sites`:
    - `GET ""`;
    - `GET "/folders"`, declared **before** `/{site_id}`;
    - `GET "/{site_id}"`;
    - `POST ""` → 201;
    - `PATCH "/{site_id}"`;
    - `DELETE "/{site_id}"` → 204.

    All of them are `AdminUser` only. Every response that holds a site sets `folder_status` with `folder_status(Path(settings.sites_root), site.folder)`.
  - Writes call `after_config_write(..., object_type="static_site", ...)` exactly as `redirection_hosts.py` does. That includes `meta={"changed": sorted(changes)}` on PATCH and `headers=response.headers` on DELETE.

- [ ] **Step 1: Write the failing folder tests (`tmp_path`)**

```python
def test_status_ok(tmp_path):            # tmp/site/index.html            -> "ok"
def test_status_no_index(tmp_path):      # tmp/site/ (empty)              -> "no_index"
def test_status_missing(tmp_path):       # tmp/other/ exists, "site" not  -> "missing"
def test_status_unmounted(tmp_path):     # root = tmp/"nope"              -> "unmounted"; empty root -> "unmounted"
def test_status_symlink_folder(tmp_path):# tmp/site -> tmp/real (os.symlink) -> "symlink"
def test_status_never_escapes(tmp_path): # root tmp/sites; tmp/secret/index.html; folder "../secret" -> "missing"
def test_list_folders_depth_and_hidden(tmp_path):
    # tmp/a/index.html, tmp/a/b/, tmp/a/b/c/ (depth 3), tmp/.git/, tmp/link -> tmp/a
    r = list_folders(tmp_path)
    assert [f.path for f in r.folders] == ["a", "a/b"]
    assert r.folders[0].has_index and not r.folders[1].has_index
def test_list_folders_not_mounted(tmp_path):
    r = list_folders(tmp_path / "nope")
    assert (r.available, r.reason, r.folders) == (False, "not_mounted", [])
```

Mark the symlink tests `skipif(os.name == "nt")`. The unreadable case is `chmod 000` as non-root; skip it when running as root.

- [ ] **Step 2: Run them; expect a FAIL on import**

Run: `pytest tests/test_static_sites_folders.py -q`

- [ ] **Step 3: Implement `folder_status` and `list_folders` in `app/services/static_site.py`**

Use `os.lstat` per component to spot symlinks, and `Path.resolve()` plus `is_relative_to` for the containment check.

- [ ] **Step 4: Run; expect PASS**

- [ ] **Step 5: Write the failing API tests**

Point `settings.sites_root` at a `tmp_path` with `monkeypatch` that holds `company/index.html`. Tests:
- `test_create_returns_201_and_status_ok`: POST `{domain_names:["s.example.com"], folder:"company"}` → 201, `folder_status == "ok"`, `crowdsec_enabled is True`, and the `X-Config-Reload-Task` header is present.
- `test_create_missing_folder_is_allowed_with_warning`: folder `"nope"` → 201, `folder_status == "missing"`.
- `test_create_rejects_escape`: folder `"../data"` → 422.
- `test_patch_changes_folder_and_audits`: an audit row with `object_type == "static_site"` and `meta.changed == ["folder"]`.
- `test_delete_204`, then `test_get_404_message`: body detail `"Static site not found"`.
- `test_unknown_certificate_or_access_list_is_422`.
- `test_folders_endpoint`: `available` true, with `[{"path":"company","has_index":true,"readable":true}]`.
- `test_folders_endpoint_unmounted`: root missing → 200, `available` false, reason `"not_mounted"`.
- `test_non_admin_forbidden`: same shape as the meg24 auth test, for list, folders and create.
- `test_access_list_delete_nulls_site`: deleting the access list leaves the site with `access_list_id is None`.

- [ ] **Step 6: Run; expect FAIL (404 on the routes)**

Run: `pytest tests/test_static_sites_api.py -q`

- [ ] **Step 7: Implement the service CRUD and the routes, and register the router**

- [ ] **Step 8: Run both test files plus `tests/test_meg24_api.py`; expect PASS**

- [ ] **Step 9: Commit** — `feat(static-sites): service, folder status and admin API`

---

### Task 3: nginx state, loader, renderer, template

**Files:**
- Modify: `backend/app/services/nginx/state.py`
  - add `StaticSiteSpec`;
  - add a `static_sites: tuple[StaticSiteSpec, ...] = ()` field on the state dataclass, next to `redirection_hosts`;
  - add `__all__`.
- Modify: `backend/app/services/nginx/__init__.py` (re-export `StaticSiteSpec`)
- Modify: `backend/app/services/nginx/loader.py`
  - `_load_static_sites(session)`: enabled rows only, with `selectinload` on the certificate and on `access_list` → auth_users/client_rules, mapped with `_access_list_spec`;
  - wire it into the state.
- Modify: `backend/app/services/nginx/renderer.py`
  - `_render_static_site(spec) -> str`;
  - in `render_config`, write `megoopm-static-{id}.conf`, plus the access list's htpasswd file when it has auth users.
- Modify: `backend/app/services/nginx/default_tls.py` (include `state.static_sites` wherever `redirection_hosts` is listed)
- Create: `backend/app/templates/nginx/static_site.conf.j2`
- Test: `backend/tests/test_static_sites_render.py` (pure render, plus `nginx -t` skipif as in `tests/test_meg24_render.py`)

**Interfaces:**
- Consumes: the Task 1 model.
- Produces:
  - `StaticSiteSpec(id: int, domain_names: tuple[str, ...], folder: str, certificate: CertificateSpec | None = None, access_list: <same AccessList spec type ProxyHostSpec uses> | None = None, ssl_forced=False, http2_support=False, hsts_enabled=False, hsts_subdomains=False, block_exploits=False, crowdsec_enabled=True, advanced_config="")`.
  - The rendered file name is `megoopm-static-{id}.conf`.

**Template shape** (`static_site.conf.j2`):
- Start from `redirect.conf.j2`. Reuse its `security`, `hsts` and `acme_challenge` macros and its listen/server_name/cert/`advanced_config | indent(4)` lines.
- Reuse `server.conf.j2`'s `access_control()` and access-phase `crowdsec()`.
  - Import or copy them into a shared include only if both templates can use it unchanged.
  - The ssl_forced `:80` redirect block uses `crowdsec_server_rewrite("w" ~ host.id)`, like the proxy template.
- Use `live_log("w" ~ host.id, host.crowdsec_enabled)`.
- Each serving block, in this order:
  1. `error_page <code> @site_<code>;` for each code;
  2. `include {{ default_dir }}/{{ errors_conf }};`;
  3. `root {{ sites_root }}/{{ host.folder }}; index index.html; autoindex off; disable_symlinks on;`;
  4. the ACME location (with `auth_basic off; allow all;` when there's an access list);
  5. `location ^~ /.well-known/ { try_files $uri =404; }`;
  6. `location ~ /\. { return 404; }`;
  7. `location / { <access_control> try_files $uri $uri/ =404; }`;
  8. for each code, `location @site_<code> { try_files /<code>.html @mgm_<code>; }` and `location @mgm_<code> { root {{ default_dir }}; try_files /{{ document(code) }} =404; }`.
- `document(code)` must produce the same file names `errors.conf.inc.j2` uses. Pass the same helper or values the renderer already gives that include.
- Don't add `=` to `error_page`, so the original status is kept.

- [ ] **Step 1: Write the failing render tests**

```python
def test_plain_site_serves_folder():       # "root /sites/company;" "disable_symlinks on;" "autoindex off;" "try_files $uri $uri/ =404;" "listen 80;" no "listen 443"
def test_hidden_paths_404_but_well_known_served():  # 'location ~ /\\. { return 404; }' and '^~ /.well-known/' and acme '^~ /.well-known/acme-challenge/'
def test_site_error_pages_come_before_branded_include():
    out = render(spec)
    for code in ERROR_CODES:
        assert out.index(f"error_page {code} @site_{code};") < out.index(ERRORS_CONF)
        assert f"location @site_{code} {{" in out and f"try_files /{code}.html @mgm_{code};" in out
def test_tls_and_forced_ssl():              # cert → "listen 443 ssl;", fullchain path; ssl_forced → :80 block has "return 301 https://$host$request_uri;" and no "root /sites"
def test_access_list_basic_auth_and_acme_open():  # auth_basic_user_file <htpasswd_path(al.id)>, acme location has "auth_basic off;"; render_config files contain htpasswd_filename(al.id)
def test_crowdsec_off_drops_bouncer():      # crowdsec_enabled=False → no megoopm_crowdsec.lua
def test_live_log_key_w():                  # 'w7' tag present for id 7
def test_render_config_file_name():         # render_config(state with one site id 3) has "megoopm-static-3.conf"
def test_disabled_site_not_loaded():        # pg-gated loader test: an enabled=False row is absent from load_state().static_sites
@pytest.mark.skipif(not shutil.which("nginx") ..., same gate as test_meg24_render)
def test_nginx_t_accepts_site_with_missing_folder():  # folder "does/not/exist", plus a TLS + access-list variant → nginx -t passes
```

Also add a single test that a site whose `certificate` is `None` but `ssl_forced=True` renders HTTP-only with no `ssl_certificate` line, the same as redirect behaves.

- [ ] **Step 2: Run them; expect a FAIL on import (`StaticSiteSpec`)**

Run: `pytest tests/test_static_sites_render.py -q`

- [ ] **Step 3: Implement the spec, the loader, the renderer and the template**

- [ ] **Step 4: Run the new tests plus `tests/test_meg24_render.py tests/test_error_pages_blocks.py tests/test_nginx_default_tls.py tests/test_live_log_render.py`; expect PASS**

- [ ] **Step 5: Commit** — `feat(static-sites): render static sites to nginx`

---

### Task 4: Runtime harness for served behavior

**Files:**
- Create: `infra/nginx/tests/static-sites.sh` (shape: `infra/nginx/tests/live-logs.sh`)

**Interfaces:**
- Consumes: `static_site.conf.j2` output from Task 3. The harness renders a config by calling `python -m` on a tiny render script in the backend container, or by writing a fixture conf rendered once. Pick whichever `live-logs.sh` already does.

- [ ] **Step 1: Write the harness**

It starts the OpenResty image with `-v <fixture>:/sites:ro` and the rendered config. The fixture holds:
- `site/index.html`, `site/about/index.html`, `site/404.html`;
- `site/.git/config`, `site/.well-known/security.txt`;
- `site/link -> /etc/passwd`;
- `bare/index.html`, which has no 404.html.

Assertions with `curl -s -o body -w '%{http_code}'`:
- `/` → 200 with the index body; `/about` → 301 to `/about/`, then 200;
- `/nope` → **404** with the site's 404.html body;
- on `bare`, `/nope` → **404** with the branded body (grep a string the branded 404 document contains);
- `/.git/config` → 404; `/.well-known/security.txt` → 200;
- `/link` → 403;
- `/about.html` → 404, so there is no extension fallback.

It exits non-zero on the first failure and prints `PASS static-sites` at the end.

- [ ] **Step 2: Run it; expect every assertion to pass**

Run: `bash infra/nginx/tests/static-sites.sh`
Expected: `PASS static-sites`.

If a status is wrong, fix the template from Task 3 and re-run the Task 3 tests, then commit the fix with this task. Ledger the change as a ruling if it alters the template shape.

- [ ] **Step 3: Commit** — `test(static-sites): nginx harness for served files and error pages`

---

### Task 5: Live logs key, CrowdSec host kinds, dashboard count

**Files:**
- Modify: `backend/app/services/live_logs.py` (`_HOST_KEY` letter class `[prds]` → `[prdsw]`, and `parse_host_key` docs)
- Modify: `backend/app/templates/nginx/_live_log.j2` (add `w` = static site to the key-letter comment)
- Modify: `backend/app/api/routes/crowdsec.py` (`_HOST_KINDS` gets `("static", StaticSite)`)
- Modify: `backend/app/services/dashboard/summary.py` and `backend/app/schemas/dashboard.py` (`static_sites: int`)
- Test: extend `backend/tests/test_live_logs*.py` (whichever covers `parse_host_key`), the CrowdSec hosts test `tests/test_crowdsec_waf_hosts_pg.py`, and the dashboard summary test

- [ ] **Step 1: Write the failing tests**
  - `parse_host_key("w12")` returns the static-site key; `parse_host_key("x12")` is still rejected.
  - The CrowdSec hosts listing includes a static site with kind `"static"`.
  - The dashboard summary counts `static_sites == 1` after one is created.
- [ ] **Step 2: Run; expect FAIL**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run those test files; expect PASS**
- [ ] **Step 5: Regenerate the OpenAPI types**

Run, in this order:
- `cd backend && python -m scripts.export_openapi` (in the container, writing `backend/openapi.json`);
- then `cd frontend && npm run gen:api`.

Commit `backend/openapi.json` and `frontend/src/lib/api/generated/schema.ts`.

- [ ] **Step 6: Commit** — `feat(static-sites): live-log key, CrowdSec and dashboard integration`

---

### Task 6: Compose mount, env and docs

**Files:**
- Modify: `docker-compose.yml`, `docker-compose.dev.yml`, `docker-compose.ha.yml` (add `- ${SITES_PATH:-./sites}:/sites:ro` to the `nginx`, `backend` and `worker` services)
- Modify: `.env.example`, `.env.ha.example` (`SITES_PATH=./sites`, with a comment)
- Create: `sites/.gitkeep`, so the default bind source exists. Add `sites/*` and `!sites/.gitkeep` to `.gitignore`.
- Create: `docs/static-sites.md`
- Modify: `README.md` (one line plus a link) and `docs/ha.md` (HA: put `SITES_PATH` on the shared mount)

- [ ] **Step 1: Write the docs.** `docs/static-sites.md` covers:
  - the folder layout and how to copy files in (scp, rsync, `git pull` inside the folder);
  - that files must be world-readable (`chmod -R o+rX`);
  - that symlinks are not served;
  - that hidden files return 404, except `/.well-known/`;
  - `/about` → `about/index.html`;
  - the site's `<code>.html` error pages and the fallback;
  - that the folder warnings are advisory;
  - HA: the shared mount.
- [ ] **Step 2: Verify the compose files**

Run: `docker compose -f docker-compose.yml config -q && docker compose -f docker-compose.dev.yml config -q && docker compose -f docker-compose.ha.yml --env-file .env.ha.example config -q`
Expected: no output, exit 0. Then confirm `grep -c ":/sites:ro"` gives 3 per file.
- [ ] **Step 3: Commit** — `feat(static-sites): mount /sites read-only and document it`

---

### Task 7: Frontend Static Sites page

**Files:**
- Create: `frontend/src/lib/api/resources/static-sites.ts` (clone `redirection-hosts.ts`; BASE `/api/v1/static-sites`; add `folders()`). Re-export it from `frontend/src/lib/api/index.ts`.
- Create: `frontend/src/app/(app)/static-sites/page.tsx`
- Create: `frontend/src/components/static-sites/static-sites-view.tsx` and `static-site-dialog.tsx`, plus `.test.tsx` for each (clone the redirection-hosts files and tests)
- Modify: `frontend/src/config/nav.ts` (add "Static Sites" after "Redirection Hosts", with the lucide `FileCode2` icon, admin-only like its neighbor)

**Interfaces:**
- Consumes the generated types from Task 5: `StaticSiteRead`, `StaticSiteCreate`, `StaticSiteUpdate`, `FoldersResult`, `FolderStatus`.

**Decided UI behavior:**
- **List columns:** Domains, Folder, SSL, Access, Status.
  - A warning badge shows next to the folder when `folder_status !== "ok"`. Copy:

    | `folder_status` | Badge text |
    |---|---|
    | `missing` | "Folder not found" |
    | `no_index` | "No index.html" |
    | `unreadable` | "Can't read folder" |
    | `symlink` | "Symlinks aren't served" |
    | `unmounted` | "Sites folder not mounted" |

- **Empty state:** when `folders().available` is false, the empty state reads: "No sites folder mounted. Set SITES_PATH and mount it at /sites to add static sites." It links to `docs/static-sites.md` on the repo, using the same link style as other doc links.
- **Folder field in the dialog:**
  - It's a `SearchableSelect` listing `folders()` paths. Entries with no index.html carry the suffix "· no index.html".
  - Below it is a "Type a folder path" text toggle that swaps in an `Input`, validated client-side with the same regex as `FOLDER_RE`. SearchableSelect has no free text.
  - Help line: "Folder inside the sites folder. Put a 404.html there to use your own error page."
- **Dialog tabs:** Details (domains, folder, access list, CrowdSec, block exploits), SSL (the same as redirection), Advanced.
- **After save:** a non-`ok` `folder_status` shows a warning toast with the badge text. The save still succeeds.

- [ ] **Step 1: Write the failing tests**, cloned from the redirection-hosts tests:
  - The list renders the domain and folder.
  - The `missing` status shows "Folder not found".
  - The empty state shows when `available: false`.
  - The dialog creates a site with a picked folder, and the POST body has `folder: "company"`.
  - The typed folder `../x` shows a validation error and doesn't submit.
  - The "no index.html" suffix shows in the options.
  - Save with `folder_status: "no_index"` shows the warning toast.
  - The nav renders "Static Sites" after "Redirection Hosts" (in the existing nav test, if one exists).
- [ ] **Step 2: Run; expect FAIL**

Run: `npx vitest run src/components/static-sites`
- [ ] **Step 3: Implement the resource, the page, the view, the dialog and the nav entry**
- [ ] **Step 4: Run the static-sites tests plus the full suite, `npx tsc --noEmit`, and `npx eslint` on the changed files**

Expected: the new tests pass, and only the 41 known failures remain.
- [ ] **Step 5: Commit** — `feat(static-sites): Static Sites page and dialog`

---

### Task 8: Frontend host lists that name every kind

**Files:**
- Modify: `frontend/src/components/live-logs/live-logs-view.tsx` (`useHostOptions` adds static sites with prefix `w` and group label "Static sites"; they appear on the Requests and Errors tabs, not Security or Stream)
- Modify: `frontend/src/components/nginx-status/lib.ts` (`culpritOf`: `megoopm-static-(\d+)` → "/static-sites")
- Modify: `frontend/src/components/audit-log/audit-log-view.tsx` (object type `static_site` → "Static site")
- Modify: the dashboard inventory in `frontend/src/components/dashboard/cards.tsx` around line 258 (add the "Static sites" count)
- Test: the matching existing tests (`live-logs-view.test.tsx`, `nginx-status` lib test, audit-log view test, dashboard cards test)

- [ ] **Step 1: Write the failing assertions in each existing test file**
  - The live-logs host picker offers a static site and subscribes with key `w5`.
  - `culpritOf("… megoopm-static-5.conf:12")` links `/static-sites`.
  - The audit row shows "Static site".
  - The dashboard shows the static-site count.
- [ ] **Step 2: Run; expect FAIL**
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run the full frontend suite, tsc and eslint; expect only the 41 known failures**
- [ ] **Step 5: Commit** — `feat(static-sites): list static sites in live logs, status, audit and dashboard`
