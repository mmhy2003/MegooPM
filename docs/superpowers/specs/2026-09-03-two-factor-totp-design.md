# Two-factor authentication with an authenticator app — design

## Where this sits

Fourth of five. It reuses P1's mailer for one notice, P2's `token_version` to
end sessions, P2's rate limiter for the code-entry step, and the Fernet helper
that already guards the SMTP password.

| | subsystem | depends on |
| --- | --- | --- |
| P1 | Email delivery | — |
| P2 | Password reset | P1 |
| P3 | User invitations | P1, P2 |
| **P4** | **2FA · authenticator app — this spec** | P1, P2 |
| P5 | Passkeys | this project's login step-up |

P5 will slot a second kind of challenge into the step-up flow this project
builds, which is why the challenge is designed as *"prove a second factor"*
rather than *"enter a TOTP code"*.

## Goal

A user enrols an authenticator app from their profile and, from then on, signs
in with a password and a code. They receive recovery codes for the day the
phone is lost. Admins can see who has it on, and switch it off for someone who
has lost everything.

## What the code already does

- **`/auth/login` returns a `TokenPair`, always.** There is no intermediate
  state between "password accepted" and "session issued". This project adds
  one.
- **`TokenType` is `Literal["access", "refresh"]`.** A third kind is added.
- **`token_version`** (P2) ends every session for a user when bumped, and
  refresh refuses a stale claim. Enabling or disabling 2FA is exactly the kind
  of event that should end other sessions.
- **The rate limiter** (P2) is a fixed window in Redis with an injectable
  client, and fails closed.
- **`app/core/crypto.py`** encrypts secrets at rest with Fernet.
- **`PasswordHasher`** in `security.py` is Argon2id, already configured.
- **PyOTP 2.10 is in the backend image** but not declared in `pyproject.toml` —
  a transitive dependency of something else. This project declares it, which
  costs no bytes and stops the feature depending on a package nobody chose.
- The profile page is a column of `Card`s; the users page is a table with a
  Status column and per-row actions.

## Storage

Three columns on `users`:

| column | notes |
| --- | --- |
| `totp_secret_enc` | text, nullable — Fernet, as `smtp_password_enc` |
| `totp_enabled_at` | timestamptz, nullable |
| `totp_last_step` | bigint, nullable — see replay, below |

**A secret with no `enabled_at` is a pending enrolment.** It has been generated
and shown, but never proven to work. Login ignores it entirely. An abandoned
setup therefore never locks anyone out, and starting over simply replaces it.

One new table, `recovery_code`:

| column | notes |
| --- | --- |
| `id` | bigint |
| `user_id` | FK → users, `ON DELETE CASCADE` |
| `code_hash` | text — **Argon2id** |
| `used_at` | timestamptz, nullable |
| `created_at` | timestamptz |

**Argon2, not SHA-256.** A recovery code is ten characters — about fifty bits.
That is enough to stop guessing over the network with a rate limit in front
of it, and not enough to survive an offline attack on a fast hash if the table
leaks. P2's reset tokens use SHA-256 because they carry 256 bits; these do not.

## TOTP itself

RFC 6238: SHA-1, six digits, thirty-second steps, and a tolerance of **one
step either side** for clock drift. PyOTP does the arithmetic; the provisioning
URI is `otpauth://totp/MegooPM:<email>?secret=…&issuer=MegooPM`.

### Replay

A code is valid for up to ninety seconds under that tolerance. A code seen over
someone's shoulder, or read from a screen, must not work a second time inside
that window. `totp_last_step` records the time-step of the last code accepted;
a code whose step is not later than that is refused even if it is otherwise
correct. PyOTP does not do this; the service does.

## Enrolment is two steps

**`POST /users/me/totp/setup`** — generates a secret, stores it encrypted with
`enabled_at` null, returns `{secret, otpauth_uri}`. Calling it again replaces
the pending secret. Refused with 409 if 2FA is already enabled — turn it off
first, with a code.

**`POST /users/me/totp/enable {code}`** — verifies the code against the
*pending* secret. On success: sets `enabled_at`, generates ten recovery codes,
stores their hashes, **returns the plaintext codes once**, bumps
`token_version`, records an audit row. A wrong code leaves everything pending
and returns 400.

Enabling without proving the app works is how people lock themselves out of
the thing that was supposed to protect them. The split is the whole point.

## Login becomes a challenge when it needs to

`POST /auth/login` keeps its shape for users without 2FA. For a user whose
`totp_enabled_at` is set, the password is verified as today and then, instead
of a `TokenPair`, the response is:

```json
{ "mfa_required": true, "mfa_token": "…" }
```

The response model is `TokenPair | MfaRequired`; the frontend discriminates on
`mfa_required`.

**The `mfa_token` is a JWT** of a new type, `mfa`, valid for **five minutes**,
carrying the user id and the current `token_version`. Stateless is right here:
replaying it earns only another challenge attempt, those attempts are
rate-limited, and a `token_version` bump — from a password change or a 2FA
change — makes it dead on arrival.

**`POST /auth/mfa/verify {mfa_token, code}`** — decodes the token, loads the
user, refuses if inactive or the `tv` claim is stale, verifies the code, and
returns the real `TokenPair`. Refusals are 401 with one message.

**Rate-limited two ways**, through P2's limiter: **ten attempts per user per
five minutes** (keyed on the user id from the token) and the per-IP counter.
A six-digit space with a three-step window is roughly one chance in three
hundred thousand per guess; without a limit that is an afternoon's work.

## Recovery codes

**Ten per enrolment**, ten characters each from an alphabet without `0/O/1/I`,
displayed as `xxxxx-xxxxx`. Generated at enable and at regenerate; shown once;
never retrievable.

**A recovery code works wherever a TOTP code does.** `mfa/verify` and the
disable routes accept either, detected by shape — six digits is TOTP, anything
longer is treated as a recovery code and checked against the unused hashes.
Using one sets its `used_at`; the verify response carries
`recovery_codes_remaining` so the client can warn when it is low.

**`POST /users/me/totp/recovery-codes {code}`** — requires a valid code of
either kind, deletes every existing code for the user, mints ten new ones,
returns them once.

Argon2 verification of up to ten hashes per attempt is deliberately slow —
roughly half a second — and happens only on the recovery path, which is rare
by construction.

## Turning it off

**`POST /users/me/totp/disable {code}`** — a valid TOTP or recovery code is
required. A stolen session must not be able to strip the second factor; the
codebase's convention that "the session is the proof" stops here, because 2FA
exists precisely for the case where the session was not the user.

**`POST /users/{id}/totp/disable`** — admin, no code. This *is* the lost-phone
backstop. It clears the secret, deletes the recovery codes, bumps
`token_version`, records an audit row naming the admin, and queues the
**"Two-factor authentication was turned off on your account"** email through
P1's mailer, naming the admin who did it. If the user did not ask for this,
that email is how they find out.

Both paths bump `token_version`. So does enable. A change to how an account is
protected ends the sessions that were opened under the old rules.

## What the users page shows

`UserRead` gains **`totp_enabled: bool`**, derived from `totp_enabled_at`.
Never the secret, never a code, never even `last_step`.

A **2FA** column with an On/Off badge, and on rows where it is on, a
**Disable 2FA** action for admins with a confirmation that names the user and
says an email will be sent.

## What the profile page shows

A **Two-factor authentication** card with four states:

- **Off** — a short explanation and an *Enable* button.
- **Setting up** — the QR (`qrcode.react`, rendered from the `otpauth_uri`),
  the secret in groups of four for manual entry, and a code field. *Cancel*
  discards the pending secret.
- **Just enabled** — the ten recovery codes, once, with a copy-all action and
  a warning that this is the only time they will be shown. A *Done* button
  that requires an acknowledgement.
- **On** — *Regenerate recovery codes* and *Disable*, each asking for a code.

## What the login form does

On `mfa_required`, the email and password fields give way to a single code
field with an autofocus, an explanatory line, and a *Use a recovery code
instead* toggle that changes the placeholder and the input mode. Three wrong
codes in a row show the rate-limit message rather than a fourth attempt.

`rememberAccount` — the recent-accounts list from earlier — fires only after
the real tokens arrive, so an abandoned challenge remembers nothing.

## Error handling

- **Wrong code** on any route: 400 (enable/disable/regenerate) or 401
  (mfa/verify), one message — *"That code is not valid."* Wrong, expired,
  replayed, and already-used recovery codes are indistinguishable.
- **Stale or expired `mfa_token`**: 401, the same message as a wrong code.
  Telling an attacker the token expired is telling them the password was
  right.
- **Rate limited**: 429 with `Retry-After`. **Redis down**: 503, failing
  closed, as every rate-limited route does.
- **Setup while already enabled**: 409.
- **Enable with nothing pending**: 409.
- **Admin disabling a user who has it off**: 409.
- **The notice email failing** is the task's problem; the disable has already
  happened and is audited.

## Testing

**TOTP verification** against **RFC 6238's published test vectors** — the
secret `12345678901234567890` at the six documented timestamps — so the check
is against the standard, not against PyOTP's own output. Then: a code from
the previous step is accepted, from two steps back refused; the same code
twice is refused; a pending secret never satisfies login.

**Recovery codes**: ten are minted; each verifies once and never twice;
regenerating kills the old set; the stored value is an Argon2 hash and the
plaintext appears in no table.

**The routes**: enable with a wrong code leaves it pending; login for an
enabled user returns `mfa_required` and no tokens; verify with a right code
returns tokens and login works; verify with a wrong code is 401; the eleventh
attempt is 429; a `token_version` bump between login and verify makes the
`mfa_token` dead; self-disable without a code is 422 and with a wrong one 400;
admin disable ends sessions, sends the email, and is audited; `UserRead`
carries `totp_enabled` and nothing else.

**The frontend**: the card's four states; the QR receives the URI; the codes
render once; the login form swaps to the code step on `mfa_required` and
back on cancel; the recovery-code toggle changes the input; the users column
and the admin action, with the action absent when it is off.

**Not covered**: whether a real authenticator app produces the codes PyOTP
expects. The RFC vectors are the proxy; the manual checklist has the real
thing.

## Files

**Backend**

- `pyproject.toml` — `pyotp>=2.9` declared
- `app/models/user.py` — three columns; `app/models/recovery_code.py` (new);
  `app/models/__init__.py`
- `alembic/versions/0028_totp.py`
- `app/core/security.py` — `TokenType` gains `mfa`; `create_mfa_token`
- `app/services/totp.py` (new) — secret, URI, verify with step tracking,
  recovery-code mint and verify
- `app/services/rate_limit.py` — `check_mfa_verify(user_id, ip)`
- `app/schemas/auth.py` — `MfaRequired`, `MfaVerifyRequest`,
  `MfaVerifyResponse`
- `app/schemas/user.py` — `totp_enabled` on `UserRead`; `TotpSetup`,
  `TotpCodeRequest`, `TotpCodes`
- `app/api/routes/auth.py` — the login union; `/mfa/verify`
- `app/api/routes/users.py` — five `totp` routes
- `app/services/mail/templates/totp_disabled.{html,txt}.j2`
- `backend/openapi.json` — regenerated

**Frontend**

- `package.json` — `qrcode.react`
- `src/lib/auth/api.ts` — `LoginResult` union, `verifyMfa`
- `src/lib/auth/context.tsx` — `login` returns `MfaChallenge | null`;
  `verifyMfa`; `rememberAccount` moves to a shared finish step
- `src/components/login-form.tsx` — the code step
- `src/lib/api/resources/users.ts` — the totp calls
- `src/components/profile/totp-card.tsx` (new); `profile-view.tsx` — mount
- `src/components/users/users-view.tsx` — column and action

## Non-goals

- **Requiring 2FA for admins as a policy.** A separate setting with its own
  lockout consequences.
- **"Trust this device for 30 days."** Its own token kind and its own
  revocation story.
- **Passkeys.** P5.
- **Re-entering the password to start enrolment.** The session is the proof
  everywhere else in this codebase, and a stolen session can already change
  the password; disabling is the one exception, and it is stated above.
- **Backup by SMS or email.** Both are weaker than the thing they would back
  up.

## Open risks

**The last admin loses their phone and their codes.** Admin-disable does not
help when there is no other admin. The recovery codes are the answer, and the
profile card says so at enrolment, but a single-admin instance that ignores
them has no way back short of the database. Worth a line in the docs, and
worth the "require for admins" policy staying a non-goal until that has a
better answer.

**Clock drift beyond thirty seconds** makes every code wrong and looks like a
bug. The one-step tolerance covers the common case; a server whose clock is
minutes out will reject everything, and nothing in the UI can tell the user
why. The manual checklist includes checking the server's time.

**The `mfa_token` is bearer.** Anyone who obtains one within its five minutes
can attempt codes against it, bounded by the rate limit. That is the same
exposure as the password it proves, for a fifth of an hour.
