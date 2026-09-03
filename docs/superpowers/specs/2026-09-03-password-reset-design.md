# Password reset — design

## Where this sits

Second of five. P1 (email delivery) shipped the mailer, the templates, and the
`app_url` setting this project builds links from.

| | subsystem | depends on |
| --- | --- | --- |
| P1 | Email delivery | — |
| **P2** | **Password reset — this spec** | P1 |
| P3 | User invitations | P1, and this project's token table |
| P4 | 2FA · authenticator app | P1; this project's `token_version` |
| P5 | Passkeys | P4 |

Two things built here are reused later, which is why they are built carefully
rather than minimally: the **single-use token table** (P3 issues invitations
through it) and **`token_version`** (P4 ends sessions when 2FA is enabled or
disabled).

## Goal

A user who has forgotten their password clicks a link on the login page, enters
their address, receives an email, follows the link, sets a new password, and
signs in. Every session they had open before is ended.

## What the code already does

- **Tokens are stateless JWTs.** `TokenType` is `Literal["access", "refresh"]`,
  minted by `_create_token` and checked by `decode_token`. A JWT cannot be made
  single-use, so this project does not use one for the reset link.
- **Refresh tokens cannot be revoked.** They live seven days and nothing can end
  one early. A password change today leaves every existing session running.
- **Three routes already change passwords:** `PUT /users/me/password` (self),
  `PUT /users/{id}/password` (admin), and the login-with-new-password path this
  project adds. None of them ends sessions.
- **There is no rate limiting**, in the app or in nginx.
- **The app never reads `X-Forwarded-For`.** uvicorn runs without
  `--proxy-headers`, so behind nginx every request's `client.host` is the
  proxy's address. Port 8000 is also published directly, so the API can be
  reached both ways.
- **`PUBLIC_ROUTES` on the frontend is `[LOGIN_ROUTE]`.** The route guard
  redirects anonymous visitors from everything else to `/login`, so the two new
  pages must be registered there or they can never be reached.
- `redis.asyncio` is already in the API process (`app/services/events.py`).

## The reset token

A row in a new `auth_token` table, not a JWT:

| column | notes |
| --- | --- |
| `id` | bigint |
| `kind` | enum `password_reset` — P3 adds `invitation` |
| `token_hash` | SHA-256 hex of the token, unique |
| `user_id` | FK → users, `ON DELETE CASCADE` |
| `expires_at` | timestamptz |
| `used_at` | timestamptz, nullable |
| `created_at` | timestamptz |

**The token itself is 32 random bytes**, url-safe base64, and is **never
stored** — only its hash is. A database leak must not hand over live reset
links, for the same reason it must not hand over passwords. SHA-256 rather than
Argon2 because the token carries 256 bits of entropy; a slow hash exists to
protect low-entropy secrets and would only slow the lookup here.

**Single use.** Redeeming sets `used_at`; a used token is refused. **One hour
to live.** Long enough for a slow mail server and a distracted user; short
enough that a link found in a mailbox next week is dead.

**Issuing a new token supersedes the old.** Every unexpired, unused token of the
same kind for that user is marked used first. Two live links for one account is
one more than anyone needs, and it is the state an attacker requesting resets
in parallel would try to create.

The table is named `auth_token` and carries a `kind` column rather than being
`password_reset_token`, because P3's invitations are the same shape — a
single-use, expiring, hashed secret bound to a user — and a second table would
be a copy.

## The routes

All three are unauthenticated.

**`POST /auth/forgot-password`** — body `{email}`. Looks the address up, and if
it belongs to an **active** user, issues a token and queues the email. Returns
**202 with the same body regardless** — see below.

**`POST /auth/reset-password`** — body `{token, new_password}`. Finds the row by
hash; refuses if absent, expired, or used. Sets the password through the
existing `user_service.set_password`, marks the token used, bumps
`token_version`, queues the changed-password notice, records an audit row with
the user as actor. Returns 204.

**`GET /auth/capabilities`** — returns `{"password_reset": bool}`, true only when
`smtp_enabled` is on **and** `app_url` is set. Without an `app_url` there is no
link to build, and without SMTP there is no way to send it. The login page hides
"Forgot password?" when this is false.

This endpoint leaks one bit to anonymous visitors: whether email is configured.
That is judged cheaper than a user clicking, being told to check their inbox,
and nothing ever arriving.

## Never revealing whether an address exists

`forgot-password` returns the identical status and body for a registered
address, an unknown one, and an inactive account: *"If that address is
registered, a reset link is on its way."* Otherwise the login page becomes a
directory of who has an account, for anyone who can reach it.

**Response time still leaks a little**: the registered case does a database
write and a queue push that the unknown case does not. Closing that fully means
performing equivalent fake work on every miss, which is more machinery than the
signal is worth on an admin panel. The spec records the gap rather than
pretending it is closed.

## Rate limiting

Two counters in Redis, checked before any work is done:

| key | limit | window |
| --- | --- | --- |
| per address, `sha256(lower(email))` | 3 | 1 hour |
| per client IP | 10 | 1 hour |

`INCR` then `EXPIRE` on first hit. Exceeding either returns **429** with a
`Retry-After` header. The per-address limit is the real defence — it caps how
many emails one inbox can receive. The per-IP limit stops one client cycling
through addresses.

**Why this is not optional.** An unauthenticated endpoint that sends email is an
outbound spam cannon: request resets for a thousand addresses and the mail
server delivers to all of them. That is how a sending domain ends up on a
blocklist, and it is a fifteen-line fix with Redis already present.

**If Redis is unreachable the endpoint fails closed** — 503, not "allow
everything". This is a security control on a security appliance; degrading it
silently is the wrong default, and a Redis outage already takes Celery with it.

### Which IP

`request.client.host` is the proxy when the request came through nginx and the
real client when it came to port 8000 directly. The rule: **if the connecting
address is in a private range (RFC 1918, loopback), trust the rightmost address in
`X-Forwarded-For` — the one nginx itself appended; otherwise use the
connecting address.** A public client
cannot spoof its way past this — its header is ignored — and a LAN attacker
forging the header is outside a rate limit's threat model.

This lives in one helper, `client_ip(request)`, with tests for both paths. It
is the first place the app reads the header, so it is written to be reused
rather than inlined.

## Ending sessions: `token_version`

A password reset is what someone does when they believe they are compromised.
Today the attacker's session survives it for up to seven days.

- `users` gains `token_version INTEGER NOT NULL DEFAULT 0`.
- Both token types gain a `tv` claim carrying the value at issue time.
- `POST /auth/refresh` loads the user and refuses with 401 when the claim does
  not match the column. Access tokens are not checked — they live minutes, and
  a database read on every authenticated request is a price not worth paying
  for that window.
- The version is **bumped by every path that changes a password**: the emailed
  reset, `PUT /users/me/password`, and `PUT /users/{id}/password`. Three ways to
  change a password, one of which ends sessions and two of which do not, is a
  rule nobody would remember.
- It is **not** bumped on deactivation, and does not need to be: `/auth/refresh`
  already refuses an inactive user outright, so `is_active = false` ends
  refresh today. An earlier draft of this spec claimed otherwise; the test
  written for it passed before any implementation existed, which is how the
  claim was found to be wrong. The test stays, as a guard on a guarantee that
  was previously only assumed.

A stale `tv` on refresh is indistinguishable, to the client, from an expired
refresh token: it lands on the login page. That is the intended outcome.

## After a successful reset

**The user is sent to the login page, not signed in.** The token arrived by
email; spending it to mint a session would extend the trust placed in that
mailbox one step further than it needs to go. The account list on the login
page already prefills their address, so it costs them one field.

## Emails

Two, both through the mailer P1 built:

- **The reset link** — `{app_url}/reset-password?token=…`, with the one-hour
  expiry stated in the body. Sent on `forgot-password`.
- **"Your password was changed"** — sent on `reset-password`. This is what
  tells a victim that someone else completed a reset they did not ask for. It
  contains no link, so it cannot itself be phished.

Both go through **the first real Celery task**, `app/tasks/mail.py`, which P1
deferred for lack of a caller. It reads the SMTP config, renders, and sends —
so a slow or dead mail server never fails the HTTP request. **It must be added
to `TASK_MODULES`** in `celery_app.py`; the dashboard shipped with a task in
`beat_schedule` but not in that list, and the symptom was three empty cards
and `Received unregistered task` in the worker log.

The task retries three times with backoff on any SMTP error, then gives up and
logs. A reset email that never arrives after a mail-server outage is the user
clicking the link again, which the rate limit permits.

## Frontend

- **"Forgot password?"** below the login form, rendered only when
  `capabilities.password_reset` is true. Fetched once on mount.
- **`/forgot-password`** — one email field. On submit shows the neutral
  message and nothing else, whatever the outcome. On 429, says to wait.
- **`/reset-password`** — reads `token` from the query string; new password
  and confirmation. On success, a message and a link to `/login`. On a refused
  token, says the link has expired or was already used, and offers the
  forgot-password page again.
- Both added to **`PUBLIC_ROUTES`** in `session.ts`, or the guard bounces them
  to `/login`.
- The password fields reuse `MIN_PASSWORD_LENGTH` and `validateNewPassword`
  from `components/users/lib.ts`.

## Error handling

- **Refused token** (absent, expired, used): 400 with one message for all
  three. Distinguishing them tells an attacker which guesses were once valid.
- **429** carries `Retry-After` in seconds so the page can say how long.
- **`MailNotConfigured`** cannot reach the route — `capabilities` gates the
  link, and `forgot-password` checks the same condition and returns the
  neutral 202 without queuing anything.
- **Redis down**: 503 from the two rate-limited routes only. Nothing else in
  auth touches Redis.
- **The Celery task failing** is logged, retried, then dropped. The HTTP
  request already returned; there is no one to tell.

## Testing

**The token** carries the risk: expired refused, used-twice refused, a
tampered token refused, an unknown token refused with the same message as all
three, issuing a second token kills the first, and the stored value is a hash —
the raw token never appears in the table.

**Enumeration**: the response body and status for a real, an unknown, and an
inactive address are byte-identical.

**Rate limiting**: the fourth request for one address is 429; the eleventh
from one IP is 429; both counters expire; Redis unreachable is 503, not 202.

**`client_ip`**: a private connecting address with `X-Forwarded-For` yields the
forwarded address; a public one ignores the header.

**`token_version`**: a refresh with a stale `tv` is 401; each of the three
password paths bumps it; deactivation ends refresh (already true, now
guarded); an access token with a stale `tv` still works until it expires.

**The task is registered.** The existing guard in `test_analytics_tasks.py`
checks only tasks named in `beat_schedule`; this one is dispatched with
`.delay()` and would slip past it. So a new test calls
`celery_app.loader.import_default_modules()` — what a worker does at startup —
and asserts `app.tasks.mail.send_email` is in `celery_app.tasks`. Without that,
a missing `TASK_MODULES` entry is invisible until the first reset email
silently never sends.

**Frontend**: the link is absent when capabilities says so; the forgot page
shows the same message on success and on an unknown address; the reset page
refuses mismatched passwords before sending anything.

## Files

**Backend**

- `app/models/auth_token.py` (new), `app/models/user.py` (`token_version`)
- `alembic/versions/0025_auth_tokens.py`
- `app/core/security.py` — the `tv` claim on both token types
- `app/core/client_ip.py` (new)
- `app/services/auth_tokens.py` (new) — issue, redeem, supersede
- `app/services/rate_limit.py` (new)
- `app/services/user.py` — bump on `set_password`, `change_own_password`
- `app/api/routes/auth.py` — the three routes; the `tv` check on refresh
- `app/tasks/mail.py` (new); `app/core/celery_app.py` — `TASK_MODULES`
- `app/services/mail/templates/password_reset.{html,txt}.j2`,
  `password_changed.{html,txt}.j2`
- `backend/openapi.json` — regenerated

**Frontend**

- `src/app/forgot-password/page.tsx`, `src/app/reset-password/page.tsx`
- `src/components/auth/forgot-password-form.tsx`, `reset-password-form.tsx`
- `src/components/login-form.tsx` — the link
- `src/lib/auth/session.ts` — `PUBLIC_ROUTES`
- `src/lib/auth/api.ts` — the three calls, beside `login` and `refresh`

## Non-goals

- **Account lockout after failed logins.** A different control, with its own
  denial-of-service trade-off.
- **CAPTCHA.** The rate limit is the defence here.
- **Changing an email address.** Its own flow, with verification of the new
  address.
- **Revoking access tokens early.** They live minutes; see above.
- **Closing the timing side-channel** on `forgot-password`. See above.

## Open risks

**`token_version` is checked on refresh only.** An attacker holding a live
access token keeps it until expiry — by default a short window, but a
deployment that raised `access_token_expire_minutes` widens it without knowing
this is what they widened.

**The private-range rule for `X-Forwarded-For` assumes nginx is the only
private-range client.** An operator who puts a second proxy in front, on a
public address, gets per-IP limits keyed on that proxy. The helper is one place
to fix, and the per-address limit still holds.

**A mail-server outage during the one-hour window** means the link arrives
dead. The user requests again; the rate limit allows three. Acceptable, and
the alternative — a longer expiry — costs more than it saves.
