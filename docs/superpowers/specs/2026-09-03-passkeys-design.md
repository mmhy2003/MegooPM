# Passkeys as a second factor — design

## Where this sits

Fifth of five, and the last. It slots a second kind of answer into the
challenge step P4 built: after the password, a user with 2FA on may satisfy
the challenge with a passkey instead of a code.

| | subsystem | depends on |
| --- | --- | --- |
| P1 | Email delivery | — |
| P2 | Password reset | P1 |
| P3 | User invitations | P1, P2 |
| P4 | 2FA · authenticator app | P1, P2 |
| **P5** | **2FA · passkeys — this spec** | P4 |

## Goal

A user who has an authenticator app enrolled can register passkeys from their
profile — Touch ID, Windows Hello, a phone, a security key — and answer the
sign-in challenge with one instead of typing a code. Recovery codes and the
admin backstop from P4 cover the day everything is lost.

## Decisions taken with the user

- **Second factor only.** Passwordless sign-in is out of scope.
- **Passkeys require TOTP first.** The 2FA switch stays `totp_enabled`;
  passkeys are additional authenticators for the challenge, never the base.
- **py_webauthn, Redis challenges, `@simplewebauthn/browser`.**

## What the code already does

- **`/auth/login` returns `TokenPair | MfaRequired`** (P4). `MfaRequired`
  carries a five-minute `mfa_token` whose `sub` is the user and `tv` the
  token version.
- **`/auth/mfa/verify`** exchanges the `mfa_token` plus a code for the real
  pair, behind `rate_limit.check_mfa_verify(user_id, ip)`.
- **`totp.verify_code(db, user, code)`** accepts a TOTP or a recovery code and
  is the one gate for "prove you hold the second factor" inside a session.
- **`totp.disable`** clears the secret, deletes the recovery codes and bumps
  `token_version`. It is called by self-disable and by the admin backstop.
- **`instance_settings.app_url`** (P1) is the app's public URL, already used
  to build email links. `GET /auth/capabilities` already reports one bit
  derived from settings.
- **`rate_limit._client()`** returns an `aioredis.Redis` from
  `settings.redis_url` with `decode_responses=True`.
- **`cryptography` 50 is in the backend image**; `webauthn` (py_webauthn)
  is not. Measured: py_webauthn 3.0.0 installs on top of it and its four
  ceremony functions accept the credential as a plain dict and
  `expected_origin` as a string or list.
- **`@simplewebauthn/browser` 14.0.0** is current on npm.

## Storage

One new table, `passkey`:

| column | type | note |
| --- | --- | --- |
| `id` | bigint | |
| `user_id` | bigint, FK users CASCADE, indexed | |
| `credential_id` | bytea, unique | the authenticator's id, raw bytes |
| `public_key` | bytea | COSE key as py_webauthn returns it |
| `sign_count` | bigint, default 0 | last value the authenticator reported |
| `name` | varchar(64) | user-chosen; "Passkey" if blank |
| `transports` | jsonb, default `[]` | hints returned at registration, replayed in `allowCredentials` |
| `created_at` | timestamptz | |
| `last_used_at` | timestamptz, nullable | |

No new columns on `users`. A user "has passkeys" when the table has rows for
them; the cap is ten.

Migration `0029_passkey`.

## Relying-party identity

Derived from `app_url` at request time, never configured separately:

- **RP ID** = the hostname of `app_url` (`localhost` in dev, `pm.example.com`
  in production).
- **Expected origin** = scheme, host and port of `app_url` with no path.
- **RP name** = `APP_NAME`.

When `app_url` is unset, every passkey route is 409 with *"Set the app URL
in Settings before adding passkeys."*, and `GET /auth/capabilities` gains
`passkeys: bool` so the profile hides the option instead of showing an error.

Ceremony parameters: attestation `none`; user verification `preferred`;
resident key `preferred`; `require_user_verification=False` on verify. The
password is the first factor; the passkey's presence is the second. No
attestation trust store, no AAGUID allow-list.

## Challenges

`app/services/webauthn_challenge.py`:

- `put(*, kind, user_id, challenge) -> nonce` stores
  `megoopm:webauthn:{kind}:{nonce}` → `{user_id}:{challenge hex}` with a
  300-second TTL. `nonce` is 32 random bytes, base64url.
- `take(*, kind, nonce) -> (user_id, challenge) | None` is a single
  `GETDEL`. A challenge is spent the moment it is read, whether or not the
  verification that follows succeeds.
- Redis unreachable raises `ChallengeStoreUnavailable`; routes answer 503
  as the rate limiter's callers already do.

`kind` is `register` or `authenticate`, so a registration challenge can never
be presented as an authentication one.

## Registering a passkey (profile)

Adding or removing a passkey requires a valid TOTP or recovery code, for the
reason self-disable does: a stolen session must not be able to give itself a
permanent second factor, and must not be able to take one away.

Routes, all requiring `totp_enabled` (409 otherwise):

- **`POST /users/me/passkeys/options`** `{code}` →
  `{nonce, options}`. Verifies the code via `totp.verify_code`, refuses at ten
  passkeys, builds registration options with `exclude_credentials` set to the
  user's existing ids, stores the challenge, returns py_webauthn's
  `options_to_json` output as `options`.
- **`POST /users/me/passkeys`** `{nonce, name, credential}` → 201
  `PasskeyRead`. Takes the challenge (400 *"That passkey could
  not be added. Try again."* when missing or belonging to another user), calls
  `verify_registration_response`, stores credential id, public key, sign
  count, transports and name. A duplicate credential id is 409. Audits
  `{"passkey": "added", "name": …}`.
- **`GET /users/me/passkeys`** → `list[PasskeyRead]`.
- **`POST /users/me/passkeys/{id}/remove`** `{code}` → 204. `POST` because
  a `DELETE` with a body is dropped by some proxies. 404 for another user's
  passkey. Audits `{"passkey": "removed"}`.

`PasskeyRead(id, name, created_at, last_used_at)`. Never the public key,
never the credential id.

Neither adding nor removing a passkey bumps `token_version`: the second
factor is still on, and the user is still the user.

## Authenticating with a passkey (login)

Two routes under `/auth/mfa`, both taking `mfa_token` and both calling
`rate_limit.check_mfa_verify` with the token's subject:

- **`POST /auth/mfa/passkey/options`** `{mfa_token}` → `{nonce, options}`.
  Decodes the token as P4's verify does (bad token → 401 with the passkey
  message), loads the user, refuses if they have no passkeys (401, same
  message), builds authentication options with `allow_credentials` set to
  their ids and transports, stores the challenge.
- **`POST /auth/mfa/passkey/verify`** `{mfa_token, nonce, credential}` →
  `MfaVerifyResponse` with `recovery_codes_remaining = null`. Takes the
  challenge, matches `credential.id` to one of the user's rows, calls
  `verify_authentication_response` with the stored key and count. Then:
  - if the stored `sign_count` is above zero and the new count is not greater,
    refuse — a cloned authenticator. Synced passkeys report zero forever,
    which is why the check is gated on the stored value.
  - update `sign_count` and `last_used_at`, issue tokens via `_issue_tokens`.

One refusal message for everything on this path: **"That passkey was not
accepted."** Bad token, spent nonce, unknown credential, failed signature and
count regression are indistinguishable to the caller.

`MfaRequired` gains **`methods: list[Literal["totp", "passkey"]]`** — always
`["totp"]`, plus `"passkey"` when the user has any — so the form knows what to
offer without a second request.

## Turning 2FA off

`totp.disable` deletes the user's passkeys alongside the recovery codes. Both
the self-disable route and the admin backstop go through it, so nothing
changes in the routes. The `totp_disabled` email's wording already says
"two-factor authentication was turned off" and needs no change.

## What the profile page shows

A new **Passkeys** card, rendered only while `user.totp_enabled` and
`capabilities.passkeys` are both true, below the 2FA card:

- A list: name, "Added <date>", "Last used <date>" or "Never used", and a
  Remove button per row. Empty state: one sentence saying what a passkey is.
- **Add a passkey**: asks for a code, then a name (placeholder "This
  MacBook", optional), then runs `startRegistration` with the options, then
  posts the credential. Success adds the row and toasts.
- **Remove**: asks for a code, then posts.
- The browser prompt being cancelled (`NotAllowedError`) is a quiet inline
  note, *"No passkey was added."*, not a red error. An origin or RP mismatch
  (`SecurityError`) shows *"This page's address does not match the app URL in
  Settings, so passkeys cannot be used here."* — the one error a user can do
  nothing about and must be told the real cause of.

The 2FA card is unchanged.

## What the login form does

On the challenge step, when `methods` includes `passkey`:

- A **Use a passkey** button above the code field. The code field keeps
  focus, so a user who reaches for the phone is not slowed down.
- Clicking it fetches options, runs `startAuthentication`, posts the
  assertion, and finishes login through the same `finishLogin` the code path
  uses. `rememberAccount` therefore behaves identically.
- A cancelled prompt returns the user to the code field with no error. A
  refusal shows the passkey message in the same alert slot the code error
  uses. 429 shows the existing "Too many attempts" text.

## Error handling

| situation | status | message |
| --- | --- | --- |
| app URL unset (profile routes) | 409 | Set the app URL in Settings before adding passkeys. |
| 2FA off (profile routes) | 409 | Two-factor authentication is not on. |
| wrong code (options, remove) | 400 | That code is not valid. |
| ten passkeys already | 409 | You can have up to 10 passkeys. |
| duplicate credential | 409 | That passkey is already registered. |
| registration failed, nonce spent, wrong user | 400 | That passkey could not be added. Try again. |
| another user's passkey (remove) | 404 | Passkey not found |
| any login-path refusal | 401 | That passkey was not accepted. |
| limiter | 429 | as P4 |
| Redis down | 503 | as P4 |

## Testing

**Backend**

- `test_webauthn_challenge.py`: put/take round trip against the fake Redis;
  a second take is `None`; a wrong kind is `None`; Redis down raises.
- `test_passkeys_service.py`: RP ID and origin derived from
  `http://localhost:3000`, `https://pm.example.com`, and
  `https://pm.example.com:8443/some/path`; refusal on unset app URL.
- `test_passkeys_api.py`, SQLite-backed with the two library verify functions
  monkeypatched on `app.services.passkeys` to return verified results:
  options require a code and 2FA; the cap; the nonce is single-use; a
  nonce from another user fails; duplicate credential 409; list never
  carries the key; remove requires a code and is 404 across users; login
  options need passkeys; verify issues working tokens; a spent nonce is 401;
  count regression is 401 only when the stored count is above zero;
  `MfaRequired.methods` reflects the rows; `totp.disable` and the admin
  route delete passkeys; the limiter fires.
- **Two real-cryptography tests** in `test_passkeys_crypto.py`: build a
  "none" attestation object and a signed assertion with an EC P-256 key from
  `cryptography` and CBOR from `cbor2`, and run them through the unpatched
  service. These prove the wiring to py_webauthn — challenge bytes,
  origin string, key encoding — rather than assume it.

**Frontend**

- `passkeys-card.test.tsx` with `@simplewebauthn/browser` mocked: hidden
  without capability; add flow calls options then registration then create;
  cancel shows the quiet note; `SecurityError` shows the origin sentence;
  remove asks for a code.
- `login-form.test.tsx`: the button appears only with the method; clicking
  runs options → start → verify and then replaces the route; a refusal stays
  on the step.

## Files

**Backend**

- `pyproject.toml` — `webauthn>=3.0`
- `app/models/passkey.py` (new); `app/models/__init__.py`;
  `tests/conftest.py` table list
- `alembic/versions/0029_passkey.py`
- `app/services/webauthn_challenge.py` (new)
- `app/services/passkeys.py` (new) — RP derivation, options builders,
  verify-and-store, verify-and-count, list, remove, `delete_all`
- `app/services/totp.py` — `disable` also deletes passkeys
- `app/schemas/auth.py` — `MfaRequired.methods`, `PasskeyOptionsRequest`,
  `PasskeyOptions`, `PasskeyVerifyRequest`; `AuthCapabilities.passkeys`
- `app/schemas/user.py` — `PasskeyRead`, `PasskeyRegisterRequest`
- `app/api/routes/auth.py` — capabilities bit; `/mfa/passkey/options`,
  `/mfa/passkey/verify`
- `app/api/routes/users.py` — four `passkeys` routes
- `backend/openapi.json` — regenerated

**Frontend**

- `package.json` — `@simplewebauthn/browser`
- `src/lib/auth/api.ts` — `passkeyOptions`, `passkeyVerify`; `MfaChallenge`
  gains `methods`
- `src/lib/auth/context.tsx` — `login` returns `{mfaToken, methods}`;
  `verifyPasskey(mfaToken)` runs the whole ceremony and finishes login
- `src/components/login-form.tsx` — the button
- `src/lib/api/resources/users.ts` — the passkey calls
- `src/components/profile/passkeys-card.tsx` (new); `profile-view.tsx` —
  mount

## Non-goals

- **Passwordless / usernameless sign-in.** Discoverable-credential login is
  its own flow with its own account-discovery questions.
- **Passkeys without TOTP.** Decided above.
- **Attestation verification or authenticator allow-lists.** No policy needs
  it.
- **Multiple RP origins.** One app URL, one origin. A deployment reached under
  two hostnames must pick one for passkeys.
- **Renaming a passkey.** Remove and add again.

## Open risks

- **Secure context.** WebAuthn works on `localhost` over HTTP and otherwise
  only over HTTPS. A production deployment reached over plain HTTP will see
  the browser refuse, and the card must say so rather than show a generic
  error.
- **Origin must match exactly**, port included. Reaching the app by IP or
  an alternate hostname while `app_url` names another breaks passkeys for
  that session; the `SecurityError` message above is the mitigation.
- **Synced passkeys and sign counts.** Apple and Google report zero; the
  regression check is gated on the stored count for that reason, which means
  clone detection covers only hardware keys that count.
- **The registration challenge is spent on first use.** A user who cancels
  the browser prompt must start again from the code. The card handles this
  by re-fetching options when the prompt is retried.
