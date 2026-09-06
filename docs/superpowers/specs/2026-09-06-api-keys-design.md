# API keys — design

**Status:** approved 2026-09-06
**Author:** brainstormed with Claude Opus 5

## The problem

MegooPM's API is reachable only with a browser session's access token: a JWT
that lives fifteen minutes and is renewed by a refresh cookie. Nothing a script
can hold. Anyone automating MegooPM today has to drive the login endpoint with a
human's password — and if that human has 2FA on, they cannot automate it at all.

An API key is the machine-shaped credential that is missing: long-lived, owned
by one user, named, revocable, and visible in the audit log for what it is.

## What this is not

Not an OAuth service, not per-key scopes, not machine accounts, not a public
developer platform. One user, some keys, the same permissions that user already
has. Everything below is sized to that.

## Decisions

### A key acts as its owner, minus everything about people

A key carries no permission its owner lacks. An admin's key can do admin things;
a member's key is read-only — not by a new rule, but because the existing
`ROUTE_ROLES` matrix already says so and key authentication changes nothing
about it.

One boundary is added on top, stated as a sentence rather than a list:

> **Keys manage infrastructure, not people.**

Every authenticated route under `/api/v1/auth` and `/api/v1/users` refuses a
key, with the two "whoami" reads exempted so a script can verify its credential
works:

| Allowed to a key | Refused to a key |
| --- | --- |
| `GET /auth/me`, `GET /users/me` | Everything else under `/auth` and `/users` |

A refusal is **403**, not 401: the caller authenticated successfully and is not
permitted, and answering 401 would send a script into a pointless
re-authentication loop. The detail names the rule ("API keys cannot manage
accounts or users; use a browser session").

That covers the password change, TOTP setup/enable/disable, recovery codes,
passkey registration and removal, the API-key routes themselves, and all admin
user administration.

The reasoning is about what a leaked key can *become*. Configuration damage is
recoverable, audited, and stops the moment the key is revoked. But a key that
can change its owner's password, disable their 2FA, mint a second key, or create
a new admin account is no longer a leaked credential — it is a permanent,
self-renewing account takeover that survives revoking the key you know about.
The boundary costs one dependency and rules that out.

A key does bypass 2FA at request time. That is what a machine credential is; the
mitigations are that it can only be created from an already-authenticated
browser session, and that it can be disabled, expired, or deleted from that same
session.

### SHA-256, not Argon2 — deliberately the opposite of recovery codes

A token is `mgm_` followed by 43 URL-safe base64 characters: 32 bytes from
`secrets.token_urlsafe`. We store the SHA-256 hex digest and never the token.

`recovery_code` uses Argon2id, and its docstring explains why: a recovery code is
ten characters, about fifty bits, which does not survive an offline attack on a
fast hash. An API key is 256 bits of CSPRNG output. There is nothing to brute
force — and Argon2 costs roughly 60ms and 64MB *per verification*, which here
means per API request. Using it would turn every authenticated call into a
self-inflicted denial of service while buying nothing.

This contrast must be recorded in the model's docstring, or someone will
eventually "fix" the inconsistency in the wrong direction.

### The same Authorization header

A key is sent as `Authorization: Bearer mgm_…`. `get_current_user` branches on
the `mgm_` prefix: a token with it is a key, anything else is a JWT as today.

Every one of the ~130 existing routes then accepts keys with no per-route
change, and Swagger's Authorize box works unchanged. A separate `X-API-Key`
header would buy only disambiguation, which the prefix already provides, and
would cost a second security scheme in the OpenAPI document.

### The audit marker travels in a ContextVar

Audit rows record `actor` as a string, set at 24 call sites that pass
`something.email`. A key-authenticated request must be distinguishable in that
log — `alice@example.com (key: CI deploy)`.

`get_current_user` sets a request-scoped `ContextVar` holding the key's name;
`record_audit` reads it and appends the suffix. No call site changes, and no
future call site can forget.

The alternative — editing all 24 sites to pass a decorated actor — was rejected
because it is correct exactly until someone adds the 25th. Implicit context is
the price; a guarantee that cannot drift is what it buys. The var is set per
request by the dependency and never inherited by a Celery task, where it reads
as unset and audit behaves exactly as it does today.

## Data model

One table, shaped like `passkey` — the existing per-user credential row.

```
api_key
  id             BIGSERIAL PK
  user_id        BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, indexed
  name           VARCHAR(64)  NOT NULL
  token_prefix   VARCHAR(16)  NOT NULL UNIQUE          -- "mgm_a1b2c3d4", shown in the UI
  token_hash     VARCHAR(64)  NOT NULL                 -- sha256 hex of the whole token
  enabled        BOOLEAN      NOT NULL DEFAULT true
  expires_at     TIMESTAMPTZ  NULL                     -- NULL means never
  last_used_at   TIMESTAMPTZ  NULL
  created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
```

`ON DELETE CASCADE`: deleting a user must take its keys with it, or a deleted
account leaves working credentials behind.

`token_prefix` is the first 12 characters of the token — the literal `mgm_`
plus the first 8 characters of the secret — unique and indexed;
this is the lookup handle, so verification is one indexed single-row read plus
one constant-time digest compare. Twelve base64 characters is 72 bits, so a
collision is not a practical event; generation retries once on the unique
violation rather than pretending it cannot happen.

**Twenty keys per user.** The passkey cap is ten, for the same reason: a bound
so that a session compromised for one minute cannot leave behind an unbounded
pile of persistent credentials. The 21st create answers 422.

Migration `0036_api_keys`, revising `0035_maintenance_mode`.

## Verification

In `get_current_user`, for a token starting with `mgm_`:

1. Look up `token_prefix`; no row → 401.
2. `hmac.compare_digest` the SHA-256 digest; mismatch → 401.
3. Owner missing or inactive → 401.
4. `enabled` false → 401, detail "This API key is disabled."
5. `expires_at` in the past → 401, detail "This API key expired on <date>."
6. Touch `last_used_at`, set the audit ContextVar, return the owner.

Every failure is a 401; only steps 4 and 5 say anything specific, and by then
the digest has matched, so the caller already holds the key and is told nothing
it could not already infer. Steps 1 and 2 are indistinguishable to a caller,
which is what keeps a wrong key from confirming a right one's existence.

**`last_used_at` is written at most once every 5 minutes per key.** A write on
every request would double the database cost of a read-only API call to buy a
timestamp nobody reads to the second. The dependency issues that UPDATE in its
own committed statement, because a read-only route never commits and the write
would otherwise be discarded.

## API

All four routes live under `/api/v1/users/me/api-keys`, are session-only by the
rule above, and are owner-scoped: an id belonging to someone else answers **404,
not 403**, so the endpoint never confirms that another user's key exists.

| Route | Body | Returns |
| --- | --- | --- |
| `GET /users/me/api-keys` | — | `ApiKeyRead[]`, never a token |
| `POST /users/me/api-keys` | `{name, expires_at \| null}` | `ApiKeyCreated` — the only time the token exists outside the caller |
| `PATCH /users/me/api-keys/{id}` | `{enabled}` | `ApiKeyRead` |
| `DELETE /users/me/api-keys/{id}` | — | 204 |

`ApiKeyRead`: `id`, `name`, `token_prefix`, `enabled`, `expires_at`,
`last_used_at`, `created_at`. `ApiKeyCreated` is that plus `token`.

`PATCH` carries `enabled` and nothing else — a key's name and expiry are fixed
at creation, so a row in the audit log means what it said when it was written.
An expired or disabled key stays in the list until it is deleted; disappearing on
expiry would hide the reason a script broke at exactly the moment someone is
looking for it.

Validation: `name` is trimmed, 1–64 characters, required. `expires_at` must be
in the future — a key created already expired is a mistake, not a configuration.
`null` means never.

Create, disable/enable and delete each write an audit row with `object_type`
`"api_key"` and the key's name in `meta`, so an admin reading the log sees
automation appear and disappear. The token and its digest never enter the log.

## UI

An `ApiKeysCard` in Profile, below the passkeys card, following that card's
structure (`Card` + a load callback + a `Mode` union for its dialogs).

The table: **Name**, the prefix in mono (`mgm_a1b2c3d4…`), **Created**, **Last
used** ("Never" until it is), **Expires** ("Never", or the date — styled as
expired once it passes), an **enable switch**, and a **delete** button. Empty
state explains what a key is for in one line.

**New API key** opens a dialog with the name and an expiry choice: *Never / 30
days / 90 days / Custom date*.

On success the dialog becomes a reveal panel: the token in a mono box, a **Copy**
button, and "This is the only time this key is shown." It closes only through an
explicit **I've saved it** — not Esc, not a click outside — because a stray
dismissal costs the key and the only recovery is to create another.

Members see the card as well. Their keys are read-only because their role is;
hiding it would be hiding a feature that works.

A one-line `curl` example sits under the table, so a first use does not need the
docs:

```bash
curl -H "Authorization: Bearer mgm_…" https://<your-host>/api/v1/proxy-hosts
```

## Testing

Backend:

- Token generation and verification, as units: prefix shape, digest match,
  constant-time compare, the unique-collision retry.
- A key authenticates an ordinary route end to end.
- Expired, disabled, deleted, and inactive-owner each answer 401 — four separate
  tests, because each is a different line and any one of them silently passing
  traffic is the whole feature failing open.
- A parametrized test over **every** session-only route asserting a key is
  refused, derived from the route table rather than a hand-written list, so a
  route added later is covered without anyone remembering to add it.
- A member's key still gets 403 on a write: key authentication grants nothing.
- The audit row carries the key marker.
- The `last_used_at` throttle: a second request inside the window issues no write.
- The 20-key cap, and owner isolation (another user's id → 404).
- Four new `ROUTE_ROLES` entries; the existing matrix test fails until they exist.

Frontend: `api-keys-card.test.tsx` — the list, create-and-reveal-once, the
switch, delete, and the empty state.

Docs: `docs/api-keys.md` — creating a key, using it, what a key cannot do and
why, and how to revoke one.

## Risks

**A key is a bearer credential in a config file.** Nothing here changes that; the
mitigations are expiry, the visible `last_used_at`, one-click disable, and the
audit trail. The UI should encourage an expiry rather than default to never — the
dialog offers 30 and 90 days ahead of Never.

**The `mgm_` prefix is a public marker.** A leaked key is recognisable as a
MegooPM credential by anyone scanning a repository. That is the point: it is what
lets secret scanners and the owner recognise one too, and it tells an attacker
nothing they would not learn by trying it.

**ContextVar leakage between requests.** Set in the dependency on every
authenticated request — including JWT ones, where it is set to `None` — so a
value cannot survive into the next request on a reused worker task.
