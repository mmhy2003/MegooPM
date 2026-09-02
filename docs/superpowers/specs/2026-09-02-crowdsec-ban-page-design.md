# The CrowdSec ban page — design

## Goal

Give a blocked visitor a real page instead of a bare `403`, chosen under
Settings: a MegooPM-branded page by default, any Custom Page as an
alternative, or no page at all for operators who want today's behaviour.

## Why

Nothing sets `BAN_TEMPLATE_PATH`, so a bounced request gets whatever the
bouncer does with no template. From the library's own `ban.lua`:

```lua
M.template_str = ""
M.ret_code = ngx.HTTP_FORBIDDEN
...
if template_file_ok == false and (M.redirect_location == nil or M.redirect_location == "") then
    ngx.log(ngx.ERR, "BAN_TEMPLATE_PATH and REDIRECT_LOCATION variable are empty, will return HTTP " .. M.ret_code .. " for ban decisions")
end
```

and `apply()` falls straight through to `ngx.exit(403)`. So today a blocked
visitor sees nginx's stock 403 with no explanation, and every nginx start logs
an error about the missing template. `docs/crowdsec.md` already documents the
bare 403 as the expected result.

This was deferred deliberately, recorded under Non-goals in
`2026-09-01-default-site-design.md` as "the other binding for Custom Pages".

## Non-goals

- **Per-host ban pages.** The bouncer configuration is global — the same
  constraint that already makes AppSec a global on/off rather than honouring
  the per-host `crowdsec_appsec_enabled` flag. One page for the instance.
- **A configurable HTTP status.** The library exposes `RET_CODE`; the page is
  served with `403` and that is not surfaced as a setting.
- **A captcha template.** `CAPTCHA_TEMPLATE_PATH` is a separate remediation
  with its own provider keys.
- **A redirect-on-ban mode.** `REDIRECT_LOCATION` exists, but sending blocked
  traffic elsewhere rather than answering it is unusual, and the operator did
  not ask for it.
- **Showing the visitor why they were blocked.** Not possible, and not wanted
  — see "The shipped page".

## Decisions taken during brainstorming

**Three modes, not two.** `megoopm` (default), `custom_page`, `none`. The
operator chose to keep a bare `403` reachable: a branded page advertises that
CrowdSec is running and which product sits in front, which some operators
deliberately avoid. Without `none` there would be no way back except selecting
a blank Custom Page.

**Existing installs get the MegooPM page on upgrade** — the column's server
default is `megoopm`, so blocked visitors get a real page without anyone
visiting Settings. This is a visible behaviour change and belongs in the
release notes.

### Verified before designing

**`init_by_lua` re-runs on `nginx -s reload` and re-reads the file.** Measured
in the `megoopm-nginx` image with a config that logs what it reads at init:

```
INIT-READ=VERSION-ONE     ← startup
INIT-READ=VERSION-TWO     ← after `nginx -s reload`
```

This is the fact the whole design depends on. The bouncer reads the template
once at configuration load, so had a reload not re-run init, changing the page
would have required restarting the nginx container — which MegooPM's apply
path does not do, and which would have made this a much larger change.

## How the page reaches the bouncer

`BAN_TEMPLATE_PATH` is a **fixed path** written into
`infra/nginx/crowdsec-bouncer.conf`, and the **presence of the file** decides
the behaviour:

```
BAN_TEMPLATE_PATH=/data/nginx/default/megoopm-ban.html
```

`ban.lua` already guards with `utils.file_exist(template_path)` and falls back
to a bare `403` when the file is missing. So mode `none` requires no special
handling anywhere in nginx or Lua: the backend simply does not write the file,
and the existing reconciler deletes any file left from a previous mode.

The container entrypoint runs `envsubst` with an explicit variable list
(`${CROWDSEC_LAPI_URL} ${CROWDSEC_APPSEC_URL} ${CROWDSEC_BOUNCER_KEY}`), so a
literal path passes through untouched and the entrypoint needs no change.

**Rejected: driving the path from an environment variable.** Env changes need
a container restart; a file change needs only the reload that already happens.

## Where the file lives

`/data/nginx/default/megoopm-ban.html` — the directory that already holds
`megoopm-default.html`.

This is the cheapest correct option: the directory is already a reconciliation
target under the `megoopm-` prefix, already created by data-init, and already
on the shared volume every node mounts. So there is **no new directory, no
compose change, no data-init change and no new engine target**, and switching
to `none` removes the file through machinery that already exists.

The base config includes that directory as `*.conf` from inside its
`default_server` block, so an `.html` file sitting there is never parsed as
configuration. `megoopm-default.html` already sets this precedent.

## The setting

Two columns on `instance_settings`, mirroring the default-site pair:

```python
crowdsec_ban_mode: Mapped[CrowdSecBanMode]      # megoopm | custom_page | none
crowdsec_ban_page_id: Mapped[int | None]        # FK custom_pages, ondelete RESTRICT
```

`RESTRICT`, not `SET NULL`, for the same reason `default_site_page_id` uses it:
silently changing what every blocked visitor sees is worse than refusing the
delete.

The API mirrors the default site exactly — `crowdsec_ban_mode` is required on
update, and `crowdsec_ban_page_id` must be present when the mode is
`custom_page` and is ignored otherwise.

## The shipped page

`banned.html.j2`, a sibling of the existing `congratulations.html.j2`, in the
MegooPM cyberpunk palette with light and dark support.

**It is completely static, and deliberately generic.** The library reads the
file once at init and emits it verbatim with `ngx.say`, so there is no
templating at request time: the page cannot show the visitor's IP, the
decision that matched, or how long the ban lasts. That is also the right
answer on its own merits — each of those tells someone probing the instance
how the defence behaves. The page says access is blocked and how to reach the
operator, and nothing else.

## Data flow

`DesiredState` gains `ban_page: BanPageSpec | None`, mirroring
`DefaultSiteSpec` field for field in spirit:

```python
@dataclass(frozen=True, slots=True)
class BanPageSpec:
    mode: str    # megoopm | custom_page | none
    html: str = ""
```

The split matters: for `custom_page` the **loader** dereferences the
referenced page into `html`, so the renderer never reaches into the database;
for `megoopm` the **renderer** renders `banned.html.j2` and `html` stays empty.
That is exactly how `DefaultSiteSpec` already divides `congratulations` from
`custom_page`, and keeps the whole mode matrix unit-testable without one.

`render_default_site` adds the ban file to its returned mapping — the same
directory, therefore the same reconciliation target — keyed `megoopm-ban.html`.
It emits **no key at all** for mode `none`, which is what makes the bare `403`
work.

**A `custom_page` whose page has gone missing writes no file**, degrading to a
bare `403` rather than to a blank document. The FK is `RESTRICT` so this means
the row was edited outside the API; serving an empty white page would look
like a broken deployment, while a `403` is the documented pre-existing
behaviour. Note this differs from the default site, which renders an empty
document in the same situation — there, dropping the file would change which
*host* answers, so the trade-offs point opposite ways.

## Testing

**Rendering**, for each mode: the MegooPM document is written for `megoopm`,
the referenced page's HTML for `custom_page`, and no file at all for `none` —
that last one is what makes a bare 403 work, so it is the important case.

**The loader**, that a referenced page is dereferenced into `ban_page` and that
a missing page degrades to no file rather than dropping the whole config, as
`_load_default_site` already does.

**The API**, that `custom_page` without a page id is rejected, and that
deleting a page in use is refused by the FK.

**The frontend**, that choosing `custom_page` reveals the page dropdown and
that the card saves independently of the other Settings cards.

**Not automatable:** that a genuinely banned IP is served the page. That needs
a live LAPI decision against a running stack and stays a manual check, as does
confirming the "BAN_TEMPLATE_PATH … empty" error disappears from the nginx log.

## Files

**Backend**

- `app/models/enums.py` — `CrowdSecBanMode`
- `app/models/instance_settings.py` — the two columns
- `alembic/versions/0021_crowdsec_ban_page.py` (new)
- `app/schemas/instance_settings.py` — read/update fields and validation
- `app/services/nginx/state.py` — `BanPageSpec`, `DesiredState.ban_page`
- `app/services/nginx/loader.py` — resolve the mode into a document
- `app/services/nginx/renderer.py` — emit `megoopm-ban.html`
- `app/templates/nginx/banned.html.j2` (new)

**Infra**

- `infra/nginx/crowdsec-bouncer.conf` — `BAN_TEMPLATE_PATH`

**Frontend**

- `src/components/settings/ban-page-card.tsx` (new) and its test
- `src/components/settings/settings-view.tsx` — mount the card
- regenerated API types

**Docs**

- `docs/crowdsec.md` — the expected response is no longer always a bare 403

## Open risks

**The upgrade changes what blocked visitors see.** Any operator relying on a
bare `403` — a monitoring probe asserting on the status body, say — sees a
document instead. The status code is unchanged at `403`, which is what almost
anything would actually assert on.

**A reload is required for a page change to take effect**, and that reload
happens through the normal apply path. If an operator edits the *Custom Page*
that the ban page points at, the ban page only changes on the next config
apply — editing a page does not itself trigger one. The Settings card should
say so rather than leave the operator wondering why their edit had no effect.
