# Static sites

A **static site** serves plain files (HTML, CSS, JavaScript, images) straight
from a folder, with no app behind it. There is no build step and no
single-page-app fallback. nginx serves what is in the folder, as it is.

Static sites live on the **Static Sites** page in the sidebar and are admin-only.
They take the same domain, certificate, access-list, CrowdSec, HSTS and
advanced-config settings as a proxy host.

## Where the files go

One host folder holds every site, with one sub-folder per site:

```
sites/                  <- SITES_PATH on the host, /sites in the containers
├── docs/
│   ├── index.html
│   ├── 404.html        <- optional: this site's own "not found" page
│   └── about/
│       └── index.html
└── marketing/
    └── index.html
```

`SITES_PATH` in `.env` names the host folder. The default is `./sites` next to
the compose file. Compose mounts it **read-only** at `/sites` in nginx, the
backend and the worker. MegooPM never writes to it. You pick a site's folder
in its dialog, either from the list or by typing a path such as `docs` or
`clients/acme`. Folder names use letters, digits, `.`, `_` and `-`. They are
at most 255 characters, and no part may start with `.`.

Copy files in however you like:

```bash
scp -r ./build/* server:/opt/megoopm/sites/docs/
rsync -a --delete ./build/ server:/opt/megoopm/sites/docs/
cd /opt/megoopm/sites/docs && git pull       # a clone works too
```

Changes show up on the next request. Nothing needs to be saved or reloaded.

## Rules nginx applies

- **Files must be world-readable.** nginx runs as its own user, so run
  `chmod -R o+rX sites/` after copying if your umask is strict.
- **Symlinks are never served.** This covers a linked file and a linked site
  folder alike, and a symlink answers as if the file were missing. A link
  planted in a site therefore cannot point nginx at anything outside it.
  Copy real files; don't deploy by switching a `current -> release-N` link.
- **Hidden paths return 404.** Anything with a path segment starting with `.`
  returns 404 (`.git/`, `.env`, `.htaccess`, …). The one exception is
  `/.well-known/`, so `security.txt` and app-association files work. The ACME
  challenge path stays MegooPM's, so certificates issue as usual.
- **Folders serve their `index.html`.** `/about` redirects to `/about/`, which
  serves `about/index.html`. There is no extension guessing: `/about` never
  serves `about.html`. Folders are never listed.

## Error pages

When nginx answers with an error status (400, 401, 403, 404, 500, 502, 503
or 504), it first looks for `<code>.html` at the **root of the site's
folder**, for example `404.html`, and serves it with that status. A site
without one gets MegooPM's branded page for that code, which you can edit on
the Settings page.

## Folder warnings

The list and the dialog show what the backend found at each site's folder:

| Warning | Meaning |
| --- | --- |
| no index.html | the folder exists but has no `index.html` at its root, so `/` returns 404 |
| missing | there is no such folder (yet) |
| unreadable | the folder cannot be read; check its permissions |
| symlink | the folder is a symlink, which nginx will not follow |
| not mounted | `/sites` is not mounted in the backend; check `SITES_PATH` |

The warnings are **advisory**. You can save a site whose folder is not there
yet and copy the files in afterwards. nginx still loads the config and serves
404 until the files arrive.

## High availability

Under HA every node's nginx serves the site from its own `/sites`, so
`SITES_PATH` is **required** in `docker-compose.ha.yml` and must name the same
shared directory on every node, for example `/mnt/megoopm/sites` next to
`SHARED_DATA_PATH`. If a site is copied to one node's local disk only, it
loads or 404s depending on which node answers. See [`ha.md`](ha.md).

## Live logs

Static sites appear in the **Live logs** host picker like any other host. In
the protocol their key is `w<id>` (see [`live-logs.md`](live-logs.md)).
