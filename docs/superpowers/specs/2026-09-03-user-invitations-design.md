# User invitations — design

## Where this sits

Third of five. P1 shipped the mailer; P2 shipped the single-use `auth_token`
table, the rate limiter, the client-IP rule, and the Celery send task. This
project reuses all of them and adds one enum value, one column, three routes,
one template, and two pieces of UI.

| | subsystem | depends on |
| --- | --- | --- |
| P1 | Email delivery | — |
| P2 | Password reset | P1 |
| **P3** | **User invitations — this spec** | P1, P2 |
| P4 | 2FA · authenticator app | P1, P2's `token_version` |
| P5 | Passkeys | P4 |

## Goal

An admin enters an address and a role; the invitee receives an email, follows
the link, chooses a name and a password, and signs in. The admin can see who
has not yet accepted, resend, or withdraw the invitation.

## What the code already does

- **`POST /users` creates a user with a password the admin shares out of band.**
  That flow stays. An instance with no mail server cannot invite anyone, and
  removing the only way it can add users would be a regression dressed as a
  cleanup.
- **`hashed_password` is `NOT NULL`.** An invited user therefore gets a random,
  unusable hash until they accept, rather than a schema change that every
  password check would then have to guard against.
- **`auth_token` has a `kind` column** built for this: `password_reset` today,
  `invitation` now.
- **`/auth/capabilities`** already reports whether SMTP is on and `app_url` is
  set — the exact condition under which an invitation can be sent. The Users
  page reuses it rather than adding a second endpoint that answers the same
  question.
- **`DELETE /users/{id}`** exists and cascades to `auth_token`. It is the
  revoke; there is no separate one.
- **Alembic runs each migration inside a transaction**, and Postgres refuses
  `ALTER TYPE … ADD VALUE` there. `op.get_context().autocommit_block()` exists
  for exactly this.

## Representing "invited"

One nullable column on `users`: `invited_at timestamptz`.

An invited user is a **real row from the moment the invitation is sent**, with
`is_active = false`, a random unusable password hash, and `invited_at` set.
Accepting sets the name and password, clears `invited_at`, and activates the
account.

**`invited_at IS NOT NULL` is the one definition of invited.** No status enum:
`is_active` already exists, and a second source of truth beside it is how
"off" and "invited" drift apart. The users table renders the three states from
the two columns — **Invited** when `invited_at` is set, otherwise Active or
Inactive as today.

Why a row at all, rather than a pending-invitation table? Because the address
must be reserved: two admins inviting the same person, or an invite racing a
direct create, should collide on the existing unique email constraint rather
than on new logic. And because every existing screen, guard and audit row
already knows what a user is.

## The token

`auth_token` gains `kind = invitation`. Same 32-byte secret, same SHA-256
storage, same single-use redeem, same supersede-on-reissue from P2.

**Seven days to live**, not one hour. A reset is a same-hour action by someone
sitting at the keyboard; an invitation is opened when the invitee gets to it,
which is next week as often as not.

## The routes

**`POST /users/invite`** — admin. Body `{email, full_name?, role}`. Creates the
user as above, issues a token, queues the email, records an audit row with the
admin as actor. Returns the `UserRead` with `201`.

**409 on a taken address, including one that is merely invited.** The fix for
"they never got it" is resend, not a second invitation that would create a
second row and fail on the email constraint anyway.

**Refused with 409 when email is not configured.** The button that reaches
this route is hidden in that state, so this is the belt to its braces.

**`POST /users/{id}/invite`** — admin. Resend. Refused with 409 unless the user
is still invited — an accepted user has a password, and re-inviting them would
hand anyone with their inbox a way to reset it. Issues a fresh token, which
supersedes the old, and queues the email. Returns 204.

**`POST /auth/accept-invite`** — unauthenticated. Body
`{token, full_name, password}`. Rate-limited per IP through P2's
`check_password_reset_redeem` — same purpose, a token that cannot be
brute-forced from one client. Redeems the token as `kind = invitation`; sets
the name and password; clears `invited_at`; sets `is_active = true`; records
an audit row with the new user as actor. Returns 204.

Then the invitee is sent to **the login page, not signed in.** Same reasoning
as reset: the token arrived by email, and spending it to mint a session extends
the trust placed in that mailbox one step further than it needs to go.

A refused token — absent, expired, spent, wrong kind — gets one message, and
the page tells the invitee to ask an administrator. There is **no self-service
resend** for an invitation, deliberately: the only address to send it to is
the one the person holding the link already controls.

## Revoke is delete

An invited user has never signed in, owns nothing, and is referenced by
nothing but their token, which cascades. `DELETE /users/{id}` is the revoke.
The existing lock-out guards are unaffected: an invited admin has
`is_active = false` and does not count toward the active-admin floor.

## The email

`invitation.{html,txt}.j2`. It names the app, names **who sent it**, carries
the accept link, and states the seven-day expiry. The sender's name matters:
"you've been invited to MegooPM" with no human attached is what phishing looks
like.

Sent through the Celery task from P2, so a slow mail server never fails the
admin's request.

## The admin surface

An **Invite user** button on the Users page, shown only when
`/auth/capabilities` reports `password_reset: true`. It is the identical
condition, and hiding the button is the honest response — an admin who can see
"Invite" and then learns nothing can be sent has been misled by the UI.

The dialog collects email, an optional name, and a role. The existing **New
user** dialog is untouched.

Invited rows show the **Invited** badge and gain a **Resend invite** action
beside the existing ones. The existing delete action is the revoke; its
confirmation copy says so for an invited row.

`UserRead` gains `invited_at`, so the table renders the badge and the action
from the list it already loads.

## The accept page

`/accept-invite?token=…`, registered in `PUBLIC_ROUTES`. Name, password,
confirmation — reusing `validateNewPassword` from the users module. Success
shows a message and a link to sign in. A refused token explains and points at
an administrator. No token in the URL says the link is incomplete.

## Error handling

- **Taken address**: 409, one message whether the existing user is active,
  inactive, or invited.
- **Resend for an accepted user**: 409.
- **Refused token**: 400 with the single message every other refusal uses.
- **Rate limited**: 429 with `Retry-After`. **Redis down**: 503, failing closed.
- **Email not configured** on invite: 409 — a configuration error the admin
  can fix, not a transient one.
- **The task failing** is logged and retried by the task itself; the admin's
  request already returned 201. If the email never arrives, the row says
  Invited and the admin resends.

## Testing

**The service**: an invite creates an inactive row with `invited_at` set and an
unusable password; a fresh invite for the same user supersedes the old token;
accept sets name and password, clears `invited_at`, activates; a second accept
of the same token is refused; the random placeholder hash never verifies
against anything.

**The routes**: 409 for a taken address in all three states; 409 for a resend
on an accepted user; 409 for an invite with mail unconfigured; accept ends
with a working login and a dead token; accept is rate-limited; both the invite
and the accept leave audit rows; the invite email carries the admin's name and
a link built from `app_url`.

**The frontend**: the button is absent when capabilities says so; an invited
row shows the badge and the resend action, an accepted row shows neither;
resend calls the right route; the accept page refuses mismatched passwords
before sending anything; the delete confirmation says "withdraw" for an
invited row.

**Not covered**: how the email renders in a client. Same manual checklist as
P1 and P2.

## Files

**Backend**

- `app/models/user.py` — `invited_at`
- `app/models/enums.py` — `AuthTokenKind.invitation`
- `alembic/versions/0027_invitations.py` — the column, and the enum value in
  an autocommit block
- `app/schemas/user.py` — `UserInvite`, `invited_at` on `UserRead`
- `app/schemas/auth.py` — `AcceptInviteRequest`
- `app/services/user.py` — `invite_user`, `accept_invitation`
- `app/services/auth_tokens.py` — `INVITE_TTL`
- `app/api/routes/users.py` — the two admin routes
- `app/api/routes/auth.py` — `accept-invite`
- `app/services/mail/templates/invitation.{html,txt}.j2`
- `backend/openapi.json` — regenerated

**Frontend**

- `src/lib/api/resources/users.ts` — `invite`, `resendInvite`, the types
- `src/lib/auth/api.ts` — `acceptInvite`
- `src/lib/auth/session.ts` — `ACCEPT_INVITE_ROUTE` in `PUBLIC_ROUTES`
- `src/components/users/invite-dialog.tsx` (new)
- `src/components/users/users-view.tsx` — button, badge, resend action
- `src/components/auth/accept-invite-form.tsx`, `src/app/accept-invite/page.tsx`

## Non-goals

- **Bulk invitations.** One at a time; the dialog reopens.
- **Expiry cleanup.** An expired token is a refused one; rows are tiny.
- **Changing an invited user's address.** Delete and re-invite.
- **Self-service resend from the accept page.** See above.
- **Replacing the create-with-password flow.** See "What the code already does".

## Open risks

**Two entry points to explain.** Invite when email works, New user otherwise.
The button visibility makes the rule concrete, but an admin who has only ever
seen one of them will be surprised by the other. Worth a line in the docs.

**An invited row is a user for every purpose except signing in.** It appears
in the user list, counts in inventory, and can be edited. That is the trade
for reusing every existing screen and guard. Editing the role of an invited
user before they accept is allowed and works; editing their name is
overwritten when they accept, which is the right precedence but may surprise.

**The placeholder hash is random per row.** It is never a credential, but
because it is a valid Argon2 hash the login path treats it like any other and
runs a verify against it. That is the desired behaviour — no timing shortcut
that says "this account has no real password" — and it is stated here so
nobody later "optimises" it away.
