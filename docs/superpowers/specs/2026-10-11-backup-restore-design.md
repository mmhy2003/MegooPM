# Backup & restore — design

**Status:** approved in chat, 2026-10-11

## Intent

An admin can take a copy of everything that makes this instance *this*
instance, keep copies automatically on a schedule, carry one off the server,
and put one back — including onto a fresh server after the old one is gone.

What the user asked for, verbatim in substance: a Backups page in the sidebar,
admin-only; manual and automatic backups; encrypted with the secret key already
in the environment; stored on the server at a path given by a new env variable.

Decisions taken during the brainstorm:

1. **Contents:** the database plus the TLS material — not the generated nginx
   configuration, which is rebuilt from the database.
2. **Restore:** from the UI, behind a typed confirmation.
3. **Schedule and retention:** configured in the UI, stored in the database;
   only the path and the encryption key come from the environment.
4. **Transfer:** backups can be downloaded *and* uploaded through the page.

Success looks like: a server dies; an admin installs MegooPM on a new one with
the same `SECRET_KEY`, uploads yesterday's file, restores, and the proxy hosts,
certificates, users and settings are all back without re-issuing a certificate.

## Why these contents

The instance's state is Postgres plus the shared data volume. Within the
volume, only some of it is irreplaceable:

| State | In a backup | Why |
| --- | --- | --- |
| Postgres database | yes | hosts, users, settings, audit log, encrypted secrets |
| `{nginx_certs_dir}/<id>/` | yes | private keys and chains; re-issuing hits Let's Encrypt rate limits |
| ACME account key | yes | `storage.account_key_path()` puts it inside the certificates directory, so the same copy takes it |
| CrowdSec whitelist + `config.yaml.local` | yes | authored in the UI, written to disk, not reconstructible from the DB alone |
| `/data/nginx/conf.d/*` and the default/error page files | no | generated from the database on every apply |
| `_acme-challenge` scratch files | no | transient, valid for minutes |

## Format

One file per backup, named `megoopm-YYYYMMDD-HHMMSS-<trigger>.mgb` (UTC), where
`<trigger>` is `manual`, `auto` or `upload`. The trigger is in the *name*
because `meta.json` sits inside the encrypted part: retention has to know which
files it may sweep, and the list has to show something useful, without the key.

```text
"MEGOOPMBK" 0x01   magic + format version   (clear)
salt               16 bytes                 (clear)
key_fingerprint    8 bytes                  (clear)
chunk*             4-byte big-endian length + AES-256-GCM ciphertext
terminator         4 zero bytes             (end of stream marker)
```

Plaintext inside is a `tar` holding:

- `meta.json` — written first in the tar, so it can be read without decrypting
  the rest — `created_at`, `app_version`
  (`importlib.metadata.version("megoopm-backend")`), `alembic_revision` (the
  `alembic_version` table's value at the time of the dump), `trigger`
  (`manual` | `scheduled`), `db_bytes`, `cert_bytes`;
- `db.dump` — `pg_dump -Fc` (custom format, compressed);
- `certs/…` — the certificates tree, mode bits preserved;
- `crowdsec/whitelists.yaml`, `crowdsec/config.yaml.local`.

**Encryption.** HKDF-SHA256 over `settings.secret_key` with the file's random
salt yields a 32-byte key; each 1 MiB plaintext chunk is sealed with AES-256-GCM
under a nonce of an 8-byte random per-file prefix plus a 4-byte counter. Chunks
are read and written streaming, so a multi-gigabyte backup never sits in memory.
The terminator makes truncation detectable: a file that ends without it is
rejected rather than restored in part. Any tampered chunk fails its GCM tag.

**Key fingerprint** is the first 8 bytes of
`sha256(b"megoopm-backup-v1" + secret_key)`. It is in the clear so the UI can
say "this backup was made with a different `SECRET_KEY`" *before* an admin
tries to restore it. This matters twice over: the archive cannot be decrypted
without that key, and the database inside it carries secrets (DNS credentials,
SMTP password, TOTP seeds, CrowdSec credentials) encrypted with the same key by
`app/core/crypto.py`.

New dependency in the backend image: the Postgres 16 client (`pg_dump`,
`pg_restore`, `psql`) from the pgdg repository. Chosen over reaching into the
postgres container through the docker socket because it keeps backups off the
socket and still works against a managed database.

## Storage

- `.env`: `BACKUP_PATH=./backups` — the **host** directory, bind-mounted into
  the backend and worker at `/backups` in all three compose files.
- Backend setting: `backup_dir: str = "/backups"` — the container path.
- Backend setting: `backup_max_bytes: int = 4 GiB` — cap for uploads.

**The directory is the list.** The page enumerates `*.mgb` in `backup_dir`;
there is no table of backups to drift out of sync with the disk. A file copied
in by hand appears; a file deleted on the host disappears. File names are
validated against `^megoopm-\d{8}-\d{6}-(manual|auto|upload)\.mgb$` on every
path that takes a name, so no request can escape the directory.

Each row's name, size, time and trigger come from the file name and `stat`.
The richer fields (app version, migration revision, byte counts) come from
`meta.json`, the archive's first member, so the API decrypts only the first
chunk to read them. When the key fingerprint does not match, the row shows
what the name and `stat` give, and says the key does not match.

## Schedule, retention, last run

New columns on `instance_settings` (migration `0039_backups`):

| Column | Type | Default |
| --- | --- | --- |
| `backup_frequency` | enum `backup_frequency` (`off`/`daily`/`weekly`) | `off` |
| `backup_hour_utc` | int | 3 |
| `backup_weekday` | int (0=Mon) | 6 |
| `backup_keep` | int | 7 |
| `last_backup_ok` | bool, null | null |
| `last_backup_at` | timestamptz, null | null |
| `last_backup_message` | text, null | null |

A beat entry runs `app.tasks.backup.scheduled_backup_tick` at minute 10 of every
hour; the tick reads the settings and decides whether this hour is the slot —
the pattern `crowdsec_hub_update` already uses, so a missed hour is skipped
rather than queued. Automatic backups apply retention afterwards: the newest
`backup_keep` `-auto` files are kept, older `-auto` ones deleted.
Manual backups and uploads are never swept — the copy an admin took before a
risky change must not expire because a nightly ran.

Backups are also skipped (and recorded as failed, with a message) when free
space in `backup_dir` is less than the estimated size plus 20%.

`AuditAction` gains `restore`. Create, delete, download, upload and restore all
write audit rows with `object_type="backup"`.

## API

All admin-only (`AdminUser`), all declared in `tests/test_route_authorization.py`.

| Route | Does |
| --- | --- |
| `GET /api/v1/backups` | list: name, size, created_at, trigger, app_version, `key_matches` |
| `POST /api/v1/backups` | enqueue a manual backup → `TaskEnqueued` |
| `GET /api/v1/backups/{name}/download` | stream the file (`StreamingResponse`, chunked) |
| `POST /api/v1/backups/upload` | multipart; streams to a temp file in `backup_dir`, validates the header, caps at `backup_max_bytes`, renames into place under a `-upload` name |
| `DELETE /api/v1/backups/{name}` | delete the file |
| `POST /api/v1/backups/{name}/restore` | enqueue a restore → `TaskEnqueued` |
| `GET /api/v1/backups/restore-status` | `{running, started_at, step, error}` — the one route that answers during a restore |
| `PATCH /api/v1/settings/backups` | frequency, hour, weekday, keep |

`python-multipart` is added to the backend dependencies for the upload route.

## Restore

Enqueued to the control-plane worker, holding a Redis lock so two restores (or
a restore and a backup) cannot overlap.

1. **Flag.** Set `megoopm:restore` in Redis with a TTL. A FastAPI middleware
   answers `503` with `Retry-After` to every request except
   `GET /api/v1/backups/restore-status` and `/health`, so nothing writes to the
   database mid-restore.
2. **Verify before touching anything.** Decrypt and unpack to a temp directory;
   reject on a bad magic, unknown format version, fingerprint mismatch, missing
   member, or a `meta.json` whose `alembic_revision` is not in this build's
   migration history (a backup from a *newer* MegooPM than the one running).
3. **Database.** Terminate other connections (`pg_terminate_backend`), then
   `pg_restore --clean --if-exists --single-transaction`. A failure rolls back
   and the existing database survives; the task reports it and clears the flag.
4. **Files.** Replace the certificates tree and the CrowdSec files, writing to
   temp paths beside the targets and renaming, so a crash cannot leave a
   half-written private key.
5. **Catch up.** Run `alembic upgrade head` — a backup may predate the running
   code. Then re-apply the nginx config through the existing path (`nginx -t`
   and rollback on failure, and the HA reconcile for other nodes), clear the
   flag, publish `restore.finished`.

Everyone is signed out, because the sessions table is part of the dump. nginx
keeps serving the old configuration until step 5, so proxied traffic is never
interrupted by a restore — only the admin UI is.

Progress is published on the existing event bus (`backup.started`,
`backup.finished`, `backup.failed`, `restore.started`, `restore.step`,
`restore.finished`, `restore.failed`) and mirrored in the restore-status route
for the window where the API is otherwise 503.

## UI

Sidebar: **Backups** (archive icon), directly above Settings, `adminOnly`.

`/backups` holds three parts:

1. **Take a backup** — a button, the last-run status line (ok/failed, when,
   the error if any), and the free space in the backup directory.
2. **Automatic backups** — frequency, hour (UTC), weekday when weekly, and
   "keep the last N". Saved through `PATCH /api/v1/settings/backups`.
3. **Backups** — a table (row cards on mobile, like every other list): when,
   size, manual/automatic, app version, and a warning chip when the key
   fingerprint does not match. Actions: Download, Restore, Delete (red, as
   every destructive action is). Above it, an Upload button.

**Restore dialog** states plainly what will happen — the database replaced,
everyone signed out, nginx config re-applied — shows what the file contains,
and requires typing the instance hostname to enable the button. While a restore
runs, the app shows a blocking screen polling `restore-status`, then sends the
admin to the login page.

## Security notes

- Backups contain private keys and every stored secret. `BACKUP_PATH` must not
  be inside a web-served directory; the docs say so, and the directory is
  created `0700` with files `0600`.
- Upload is the one place a file from outside becomes live state. It is
  admin-only, header-validated, size-capped, and a restore of it still has to
  pass fingerprint and format checks. A restore is never automatic.
- Download and upload are audited with the actor, as restore and delete are.

## Testing

- **Crypto:** round-trip; wrong key; truncated file (missing terminator);
  flipped byte in a chunk; empty input; a payload larger than one chunk.
- **Archive:** build and read back; mode bits on key files; a member missing
  from the tar is rejected.
- **Store:** name validation rejects traversal and odd names; listing ignores
  foreign files; retention keeps the newest N scheduled and never a manual one;
  the free-space guard.
- **Schedule:** the tick runs in the configured hour only, daily and weekly.
- **API:** admin-only matrix row for every route; the 503 middleware blocks a
  write and lets `restore-status` through; upload rejects an oversized or
  malformed file; download streams the bytes.
- **End to end against the test Postgres:** seed an instance, back it up,
  change it, restore, and assert the database matches the seeded state and the
  certificate files are back.
- **Frontend:** the page renders a list, the schedule round-trips, the restore
  dialog stays disabled until the hostname matches, and the blocking screen
  appears while a restore runs.

## Out of scope

- Off-site destinations (S3, SFTP) — the directory can be synced by the host.
- Rotating `SECRET_KEY`, or re-encrypting old backups after a rotation.
- Selective restore (one host, one certificate).
- Backing up a node's local state in HA: the control plane holds everything
  that matters, and data-plane nodes rebuild from it.
