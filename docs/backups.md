# Backups and restore

MegooPM can take an encrypted copy of everything that makes an instance *this*
instance, on a schedule or on demand, and put one back — including onto a fresh
server after the old one is gone.

Everything here lives on the **Backups** page in the sidebar, admin-only.

## What a backup contains

| In | Not in |
| --- | --- |
| The whole Postgres database | The generated nginx configuration |
| Certificates and their private keys | The ACME challenge scratch files |
| The ACME account key | Container images, `.env`, compose files |
| The CrowdSec whitelist and `config.yaml.local` | |

The nginx configuration is left out because it is rebuilt from the database on
every apply; restoring a stale copy would be worse than regenerating it.

**`.env` is not in a backup.** A restore onto a new server needs that file —
above all `SECRET_KEY` — put there by you, before the restore.

## SECRET_KEY: the one thing a backup cannot replace

A backup is encrypted with a key derived from `SECRET_KEY`, and the secrets
*inside* the database (DNS credentials, the SMTP password, TOTP seeds, CrowdSec
credentials) are encrypted with it too.

So a backup can only be read by an instance whose `SECRET_KEY` matches the one
that made it. The page shows this: a backup from a different key is listed —
you can still see and delete it — but its **Restore** button is disabled and
the row says why.

**Keep `SECRET_KEY` somewhere other than the backups.** Whoever holds both a
backup file and that key holds the instance.

## Where they are kept

`BACKUP_PATH` in `.env` is the host directory, mounted into the backend and the
worker at `/backups`:

```bash
BACKUP_PATH=./backups
```

It must not be inside a web-served directory. The directory is created `0700`
and every backup `0600`.

**The directory is the list.** The page shows what is in it, so a file you copy
onto the server with `scp` appears, and a file you delete on the host
disappears. There is no separate record to drift out of sync.

A backup's name says when it was taken and how:
`megoopm-20261011-030000-auto.mgb` — UTC, and one of `manual`, `auto` or
`upload`.

### Under HA: one directory, shared by every node

`BACKUP_PATH` must be **the same shared directory on every node**, which is why
`docker-compose.ha.yml` requires it instead of defaulting.

The page lists whatever directory the node answering your request has mounted.
Backups are taken by the worker on one node, and your load balancer decides
which node serves the page — so with a per-node path the file exists on disk
and the UI shows nothing. Pointing every node at one directory also means a
restore works from whichever node you happen to be on.

```bash
# .env, identical on every node
BACKUP_PATH=/mnt/megoopm/backups     # under SHARED_DATA_PATH
```

If a backup you took is missing from the page, this is almost always why.
Check what the node answering you can see:

```sh
docker compose -f docker-compose.ha.yml exec backend sh -c 'ls -l /backups'
```

Backups on the shared mount share that storage's fate, so copy them off-box on
a schedule — or point `BACKUP_PATH` at different storage that is still mounted
identically on every node.

## Automatic backups

Set the frequency (never / daily / weekly), the time in **UTC**, and how many
to keep. A tick runs every hour and takes a backup when the hour matches; a
missed hour is skipped rather than queued.

**Retention only ever deletes automatic backups.** The copy you took by hand
before a risky change is kept until you delete it, and so is anything you
uploaded.

A backup is refused — and recorded as a failed run, visible on the page — when
the directory does not have room for it.

## Restoring

1. Pick a backup and read what the dialog says it will do.
2. Type the instance's hostname to enable the button.
3. The worker then verifies the file completely *before* replacing anything:
   the key fingerprint, the archive's contents, and whether the backup came
   from a newer MegooPM than the one running (which is refused).
4. The database is replaced, then the certificate and CrowdSec files, then
   migrations run and the nginx configuration is re-applied.

While it runs:

- the admin API answers **503** to everything but the restore status, so
  nothing writes to a database that is being replaced;
- **proxied traffic is unaffected** — nginx keeps serving the configuration it
  already has until the apply at the end;
- the container healthcheck keeps answering, so nothing restarts mid-restore.

Sessions come from the backup too, so any sign-in made after it was taken
stops working — sign in again. For a few seconds while the database is being
replaced some requests are refused outright rather than answered; the page
polling the status retries on its own.

If the restore fails, the page says why and **the old database is still
there** — `pg_restore` runs in a single transaction that rolls back.

## Onto a fresh server

1. Install MegooPM as usual, with the **same `SECRET_KEY`** in `.env`.
2. Start the stack and sign in.
3. Backups → **Upload**, pick the `.mgb` file (or copy it into `BACKUP_PATH`
   on the host, where it will appear in the list).
4. Restore it.

Certificates come back with their private keys, so nothing is re-issued and no
Let's Encrypt rate limit is touched.

## Checking a backup by hand

```bash
ls -l "$BACKUP_PATH"
docker compose exec backend sh -c 'ls -l /backups'
```

The first 42 bytes of a `.mgb` file are a clear header (magic, format version,
salt, key fingerprint); everything after it is AES-256-GCM in 1 MiB chunks. A
file that ends without its terminator is treated as truncated and refused, so a
backup interrupted by a full disk can never be restored in part.
