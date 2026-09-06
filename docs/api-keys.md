# API keys

A key lets a script use the MegooPM API as you, with your permissions. It is
long-lived, named, revocable, and shown exactly once.

Until now the API was reachable only with a browser session's access token,
which lives fifteen minutes and is renewed by a cookie — nothing a script can
hold. A key is the machine-shaped credential that was missing.

## Creating one

**Profile → API keys → New API key.** Give it a name that says what uses it —
a key nobody can name is a key nobody dares revoke — and choose an expiry.

The token appears once, on the screen that created it. Only a hash of it is
stored, so it cannot be shown again or recovered: if you lose it, delete the key
and create another.

An expiry is offered before "Never" deliberately. A key that stops on its own is
one fewer credential outliving the job it was made for.

## Using one

Same header a browser session uses, so every endpoint accepts it:

```bash
curl -H "Authorization: Bearer mgm_…" https://<your-host>/api/v1/proxy-hosts
```

To check that a key works:

```bash
curl -H "Authorization: Bearer mgm_…" https://<your-host>/api/v1/users/me
```

## What a key cannot do

**Keys manage infrastructure, not people.** Every authenticated route under
`/api/v1/auth` and `/api/v1/users` answers **403** to a key, with two
exceptions: `GET /users/me` and `GET /auth/me`, kept so a script can verify its
own credential.

So a key cannot change your password, enable or disable your 2FA, add or remove
a passkey, create another API key, or administer users.

The reasoning is about what a leaked key can *become*. Configuration damage is
recoverable, audited, and stops the moment the key is revoked. A key that could
change its owner's password, disable their 2FA, or mint a successor would not be
a leaked credential — it would be a permanent, self-renewing account takeover
that survives revoking the key you know about.

Automating user administration therefore needs a browser session. That is the
intended trade.

## Permissions

A key never exceeds its owner. It is authentication, not authorization: the same
role checks apply as when you are signed in.

- An **admin's** key can do admin things.
- A **member's** key is read-only, because a member is.

Deactivating a user stops every key that user owns, immediately. That is an
admin's lever over someone else's automation.

## Revoking

From the same card:

- **The switch** disables a key. Reversible; useful while you find out whether
  something still depends on it.
- **Delete** revokes it for good.

Either takes effect on the key's next request.

## What is stored

A SHA-256 digest of the token and a 12-character prefix (`mgm_a1b2c3d4`), never
the token. The prefix is what the card shows, so a key can be matched to a log
line without revealing it.

SHA-256 rather than Argon2 is deliberate, and the opposite of the choice made
for recovery codes. A recovery code is about fifty bits and needs a slow hash to
survive an offline attack on a leaked table. A token here is 256 bits of
cryptographically-random data: there is nothing to brute force, and a slow hash
would cost ~60ms of every API request for no gain.

## In the audit log

A change made with a key records the key alongside the account:

```
you@example.com (key: CI deploy)
```

`Last used` on the card is updated at most once every five minutes, so a busy
script does not write a row per request.

## Limits

- 20 keys per account.
- Names up to 64 characters.
- An expiry must be in the future; "Never" is the explicit alternative.
