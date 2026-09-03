# Email delivery — design

## Where this sits

The request that produced this spec covered five subsystems: email delivery,
password reset, user invitations, 2FA with an authenticator app, and passkeys.
They were decomposed rather than specced together.

| | subsystem | depends on |
| --- | --- | --- |
| **P1** | **Email delivery — this spec** | — |
| P2 | Password reset | P1 |
| P3 | User invitations | P1, and P2's token machinery |
| P4 | 2FA · authenticator app (TOTP) | P1 for the admin-disable notice |
| P5 | Passkeys (WebAuthn) | P4's login step-up |

P1 is first because three of the other four are blocked on it and it is blocked
on nothing.

## Goal

An operator configures SMTP in Settings, sends a test message, and receives a
themed email carrying the MegooPM logo.

Nothing else sends mail yet — P2 and P3 are the first real callers. Shipping the
test send is what makes this project verifiable on its own instead of only
provable once password reset exists.

## What the code already does

There is **no email code at all**: no SMTP, no templates, no mail dependency.

Two existing patterns shape the design rather than being invented here:

- **`instance_settings` holds operator configuration**, including the LLM block
  with an encrypted `llm_api_key_enc` column, a PATCH route, and a
  `POST /settings/llm/test` that reports success or failure inline. SMTP joins
  that table in the same shape.
- **`app/core/crypto.py`** already encrypts secrets at rest: Fernet with the key
  derived as SHA-256 of the application `secret_key`. The SMTP password uses it
  unchanged.

`jinja2` and `cryptography` are already dependencies, so the templates and the
encrypted column cost **no new packages**. The mailer adds none either — see
below.

## Configuration

SMTP lives in `instance_settings`, not the env file. The original request asked
for env; the database was chosen instead because it matches the newest
configuration in the app, survives a restart, and is one place rather than one
per node in an HA cluster.

New columns:

| column | notes |
| --- | --- |
| `smtp_enabled` | bool, default false |
| `smtp_host` | text, nullable |
| `smtp_port` | int, default 587 |
| `smtp_security` | enum `starttls` \| `ssl` \| `none` |
| `smtp_username` | text, nullable |
| `smtp_password_enc` | text, nullable — Fernet, as `llm_api_key_enc` |
| `smtp_from` | text, nullable — the envelope and header From |
| `smtp_from_name` | text, nullable — display name |
| `app_url` | text, nullable — this instance's public URL |

**`app_url` is here rather than in a later project** because the operator
configuring email is the one who knows the instance's public address, and
because three later subsystems need it: P2 and P3 build links with it, and P5
derives the WebAuthn Relying Party ID from it. It is validated as an absolute
`http(s)` URL.

P1 stores and validates it but does not use it: the test email contains no
links, and the logo is embedded rather than fetched. It is here so the operator
sets it once, in the same sitting as the mail server.

A CHECK constraint in the spirit of the existing `llm_needs_model`:
`smtp_enabled = false OR smtp_host IS NOT NULL`. Enabling delivery with nowhere
to deliver is a state the database should refuse, not one the UI should merely
discourage.

**The password is never returned.** The read schema exposes
`smtp_password_set: bool`, mirroring `llm_api_key_set`.

## The mailer

`app/services/mail/`, three modules with one job each:

- `config.py` — read the settings row, decrypt the password, return a typed
  `MailConfig`; raise `MailNotConfigured` when there is no host.
- `sender.py` — build the MIME message and hand it to SMTP.
- `templates.py` — render a named template to `(subject, html, text)`.

Splitting them this way means the template tests never open a socket and the
sender tests never render Jinja.

### stdlib `smtplib`, not `aiosmtplib`

The same sending code has to run from an **async route** (the test send) and a
**sync Celery task** (every real notification). `asyncio.to_thread` bridges
sync-into-async in one line; bridging the other direction means running an event
loop inside a Celery worker.

SMTP is a simple enough protocol that the standard library is genuinely
adequate here, and this keeps the dependency count at zero for the whole
project.

## Two sending paths, deliberately

**The test send is synchronous**, with a short timeout. The operator is standing
in front of the Settings page and needs the actual SMTP error — "authentication
failed", "connection refused", "certificate verify failed" — not a task id to go
and poll. This matches how `/settings/llm/test` already behaves.

**Real notifications go through Celery.** A password-reset request must not fail
because the mail server is slow or down; the user's password is reset either
way, and the mail arrives when it arrives.

P1 does **not** build that task. It has no caller here, and a queued send with
nothing to send is speculative work. What P1 fixes is the contract: `sender.py`
is callable from either context, and P2 wraps it in a task when it has the first
real message. Recording the decision now is the point; writing the code now is
not.

## Templates

Jinja2. One `base.html.j2` owns the table layout, the header with the logo, a
body slot, and the footer; each email is a small child template supplying the
body. The base is where every cross-client workaround lives, so there is one
place to fix when a client misbehaves.

### Colour

Every colour is **inlined as hex**. A `<style>` block carries *only* the
`@media (prefers-color-scheme: dark)` overrides.

This is the honest ceiling of the medium: the dark block is honoured by Apple
Mail, iOS Mail and Outlook.com; Gmail strips `<style>` in most contexts and
applies its own automatic inversion that cannot be controlled or opted out of.
A light design that Gmail inverts reads acceptably, which is why the light
design is the base and dark is the override rather than the reverse.

`app/services/mail/palette.py` holds the transcoded values with the source
beside each:

```python
PRIMARY = "#007789"  # oklch(0.50 0.14 205)
```

The app's palette is authored in `oklch()`, which no email client supports, so
these are converted at authoring time:

| token | light | dark |
| --- | --- | --- |
| background | `#f0f9fb` | `#0a0917` |
| card | `#ffffff` | `#121123` |
| foreground | `#1b1630` | `#e0f3f4` |
| primary | `#007789` | `#00edee` |
| primary-foreground | `#f4fefe` | `#0a0917` |
| muted-foreground | `#4d5e7a` | `#89abb4` |
| border | `#b4d9e0` | `#2b2a46` |
| destructive | `#d7002d` | `#ff426d` |
| success | `#00791b` | `#8aec41` |

**This is a copy, and it will not follow `globals.css`.** A build step that
converted the tokens automatically is more machinery than nine colours justify.
Saying so in one place is better than a sync mechanism nobody maintains.

### Type

`-apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif` for text and
`ui-monospace, SFMono-Regular, Menlo, Consolas, monospace` for anything
code-shaped.

Inter is self-hosted by `next/font` for the web app and cannot load in an email
client — `@font-face` is blocked or stripped nearly everywhere. The stack above
is chosen to sit close to Inter on each platform rather than to match it.

### Plain text

**Every message is `multipart/alternative` with a real text part**, written
rather than stripped from the HTML. HTML-only mail is a spam signal, and a text
client showing an empty body is worse than a plain one showing the message.

## The logo

Embedded, not hotlinked.

A downscaled `logo-email.png` — 96px asset for a 48px display box — is committed
under `app/services/mail/assets/`. The existing `public/logo.png` is 512×512 and
180 KB; sending that with every message is roughly thirty times the bytes for no
visible difference.

It is attached as a `multipart/related` part with a Content-ID and referenced as
`<img src="cid:logo" alt="MegooPM">`.

Hotlinking `{app_url}/logo.png` fails twice over. Most clients block remote
images until the reader clicks "show images", so the header is empty on first
read. And a self-hosted instance on an internal network is not reachable from
the recipient's mail client at all — the image would never load for anyone
outside the LAN.

The `alt` text matters for the same reason: when images are blocked, the header
still says who sent this.

## Settings surface

One card on the Settings page, in the shape of the existing LLM card: host,
port, security, username, password, from-address, from-name, and the app URL.
Save, then **Send test email**, with the result shown inline.

The password field follows the LLM card's convention — a blank field means
"leave the stored one alone", and the card shows whether one is set rather than
its value.

## Error handling

- **`MailNotConfigured`** when no host is set. The test route reports it as a
  clear message, not a 500. P2 and P3 decide their own behaviour when mail is
  unconfigured; that is their spec's problem, not this one's.
- **SMTP failures** are caught and returned as `{ok: false, detail}`, the shape
  `LlmTestResult` already uses. An operator typing a wrong password should see
  "authentication failed", not a stack trace.
- **Header injection.** `smtp_from`, `smtp_from_name` and every rendered subject
  are rejected if they contain CR or LF. A newline in a display name lets an
  attacker append arbitrary headers — a `Bcc:` of their choosing — to every
  message the system sends.

## Testing

**Templates** carry most of the risk and take most of the coverage: both themes
render, the CID reference is present, the text part is non-empty, and a display
name containing markup is escaped rather than injected.

**The palette** gets one test that re-runs the oklch→hex conversion against each
committed constant. It cannot catch the copy drifting from `globals.css` —
nothing can — but it does catch a mistyped hex.

**The sender** runs against a monkeypatched `smtplib.SMTP`: the envelope
addresses, the TLS mode for each of the three security settings, and a refusal
when the subject contains CRLF. No fake SMTP server, so no dev dependency.

**The routes**: admin-only, the password is never in a response,
`smtp_password_set` flips when one is saved, and a failing test send reports the
failure without a 500.

**Not covered:** how any of this renders in a real mail client. That needs
messages sent by hand to Gmail, Outlook and Apple Mail in both light and dark
mode, and it belongs on a manual checklist rather than being implied by a green
suite.

## Files

**Backend**

- `app/models/instance_settings.py` — the new columns and the CHECK constraint
- `alembic/versions/…_smtp_settings.py` — the migration
- `app/schemas/settings.py` — `SmtpSettingsUpdate`, `MailTestRequest`,
  `MailTestResult`; `smtp_password_set` on the read model
- `app/services/settings.py` — update + decrypt helpers, beside the LLM ones
- `app/services/mail/config.py`, `sender.py`, `templates.py`, `palette.py`
- `app/services/mail/templates/base.html.j2`, `test-email.html.j2`, and the
  matching `.txt.j2` parts
- `app/services/mail/assets/logo-email.png`
- `app/api/routes/settings.py` — `PATCH /settings/smtp`, `POST /settings/smtp/test`
- `backend/openapi.json` — regenerated
- tests alongside each

**Frontend**

- `src/components/settings/smtp-card.tsx` and its test
- `src/lib/api/resources/settings.ts` — the new calls and types
- `src/components/settings/settings-view.tsx` — mount the card
- `src/lib/api/generated/schema.ts` — regenerated

## Non-goals

- **Bounce handling.** Nothing reads a mailbox back.
- **Retry policy** beyond what Celery already provides.
- **Per-recipient preferences or unsubscribe.** These are transactional
  administrative emails, not marketing.
- **DKIM, SPF, DMARC.** These are DNS records on the sending domain, configured
  outside this application.
- **Any actual notification email.** P2 and P3 bring those. P1 ships only the
  test message.
- **Keeping the email palette synchronised with `globals.css`.** See above.

## Open risks

**The palette copy drifts.** Someone changes `--primary` in `globals.css` and
emails keep the old teal. The transcode test does not catch this, because it
validates the copy against itself. The mitigation is a comment naming
`globals.css` as the source and the small number of colours involved — nine, in
one file.

**Cross-client rendering is unverifiable in CI.** Table layouts and
`prefers-color-scheme` behave differently in Outlook's Word rendering engine,
Gmail's sanitiser, and Apple Mail. The suite proves the markup is what we
intended; only a real send proves it looks right.

**An admin can point SMTP at any host and port.** This is not a new privilege —
an admin of a reverse-proxy manager already controls where traffic goes — but it
does mean the mail settings are a way to make the backend open outbound
connections to an arbitrary address. Worth knowing; not worth blocking, since
restricting it would break legitimate internal relays.
