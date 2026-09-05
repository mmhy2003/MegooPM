# Role-based access: a member who can look but not touch

**Date:** 2026-09-05
**Status:** approved for planning

## The problem

MegooPM has two roles, `admin` and `member`, but `member` was never given a
meaning. Signing in as one leaves the app apparently broken: the sidebar offers
Proxy Hosts, Certificates, Security and the rest, and every one of those pages
fails, because the endpoint behind it requires an admin.

Measured against the running app, the current state is:

| Guard | Routes |
| --- | --- |
| `admin` | 98 |
| `member` (any authenticated user) | 12 |
| public | 12 |
| **total** | **122** |

All twelve member routes are a user acting on themselves — their own profile,
password, TOTP and passkeys. **Every read of every resource is admin-only**,
including `/dashboard/summary`. So the role is not over-permitted; it is
locked out of everything, which is the opposite of the problem it looks like.

Two findings from the same survey, worth fixing here because they are part of
the same boundary:

1. **`/api/v1/tasks/sample` (POST) and `/api/v1/tasks/{task_id}` (GET) have no
   authentication at all.** They are mounted with no dependency and are
   reachable by anyone who can reach the API, signed in or not.
2. **Reads do not leak secrets**, which was checked before proposing to open
   any of them. `CertificateRead` never includes key material, and
   `DnsCredentialRead` returns the *names* of secret fields, never their
   values.

## What a member is

A member **reviews the instance and changes nothing.**

- **Reads** every page except Settings and Users: Dashboard, Proxy Hosts,
  Upstream Pools, Certificates, Access Lists, Streams, Redirection Hosts, 404
  Hosts, Custom Pages, Security.
- **Writes nothing.** No create, edit, delete or enable/disable toggle. No
  nginx reload, no certificate renewal, no Ask AI — the last of these because
  it spends real LLM credit and the result would only be saveable by an admin.
- **Still manages their own account.** Password, 2FA and passkeys on
  `/profile`, exactly as today. This is the one place a member writes, and it
  writes only to their own row.

Settings and Users remain admin-only, both in the API and as pages.

## Architecture

### The authorization matrix is the backbone

Thirty-odd route guards changed by hand is thirty-odd chances to miss one.
The safety therefore does not come from care during the edit; it comes from a
test that enumerates **every route the app exposes** and asserts the guard it
actually enforces against a declared table:

```python
ROUTE_ROLES: dict[tuple[str, str], str] = {
    ("GET", "/api/v1/proxy-hosts"): "member",
    ("POST", "/api/v1/proxy-hosts"): "admin",
    ...
}
```

The test walks the app's router tree, resolves each route's flattened
dependency list to `public` / `member` / `admin`, and fails if:

- a route is not in the table (a new endpoint must state its role), or
- a route's actual guard disagrees with the table.

This turns "did we remember?" into a question the suite answers, and makes any
future endpoint declare its role as a condition of merging.

**Why not a global default.** Mounting the API with an "authenticated"
dependency and marking writes admin was rejected: a new write route added
without the marker would silently be member-writable. It fails open. Explicit
per-route guards fail closed — a missed edit gives a member a 403 on a page
they should see, which is visible and harmless.

**Why not a method-based guard.** "GET for anyone, admin otherwise" is
concise, and new writes would be locked by default, but it is invisible at the
route signature and it breaks the cases that do not fit the shape: login is
public, and a member legitimately POSTs to change their own password and enrol
a passkey. The exceptions cost more than the rule saves.

### Least privilege: only what a member-visible page reads

A member gets read access to exactly the endpoints a member-visible page
calls. Endpoints with no UI behind them stay admin, because nothing a member
can open needs them:

| Endpoint | Role after | Why |
| --- | --- | --- |
| `/dashboard/{summary,threats,visitors}` | member | The Dashboard |
| `/proxy-hosts`, `/{id}` | member | Proxy Hosts |
| `/upstreams`, `/{id}` | member | Upstream Pools |
| `/certificates`, `/{id}` | member | Certificates |
| `/access-lists`, `/{id}` | member | Access Lists |
| `/streams`, `/{id}` | member | Streams |
| `/redirection-hosts`, `/{id}` | member | Redirection Hosts |
| `/dead-hosts`, `/{id}` | member | 404 Hosts |
| `/custom-pages`, `/{id}` | member | Custom Pages, including the preview |
| `/crowdsec/{alerts,decisions,health,maintenance,whitelists,whitelists/status}` | member | Security |
| `/tasks/{task_id}` | member | **was public** — status polling |
| `/tasks/sample` | admin | **was public** — enqueues work |
| `/audit-log`, `/events`, `/cluster/status`, `/nginx/preview` | admin (unchanged) | No page reads them |
| `/dns-providers`, `/dns-credentials` | admin (unchanged) | Read only by the certificate dialog, which is admin-only |
| `/settings/*`, `/users` (except `/users/me/*`) | admin (unchanged) | Out of scope by decision |

Every non-GET route outside `/auth/*` and `/users/me/*` stays admin.

### Frontend

The nav already hides Users and Settings from members via `navForRole`, so the
gap is inside the pages.

- **`useCanWrite()`**, a small hook over the existing auth context, returns
  `false` for a member. Each list page hides its New / Edit / Delete / toggle
  controls rather than rendering buttons that will 403. Hidden, not disabled:
  a disabled button invites a click and explains nothing.
- **A read-only note** on each page that has hidden controls, so the absence
  reads as intentional rather than broken.
- **A route guard** on `/settings` and `/users`. The nav does not link them,
  but typing the URL currently renders a page whose every request fails. A
  member is sent back to the Dashboard instead.

## Error handling

A member who reaches a write endpoint anyway — a stale tab, a hand-made
request — gets the existing 403 from `require_admin`. Nothing changes there;
the API stays the enforcement point and the UI is a convenience over it.

## Testing

- **The matrix test**, as above. It is the one that matters: it covers all 122
  routes and every route added later.
- **Per-router API tests**: a member gets 200 on the reads listed above and
  403 on each write. Using the existing `member_token` fixture.
- **The task routes**: unauthenticated requests are refused, a member may read
  a status, and only an admin may enqueue.
- **Frontend**: a member sees no write controls on a list page and an admin
  does; a member landing on `/settings` or `/users` is redirected.

## Non-goals

- No third role, and no per-resource permissions. Two roles, one boundary.
- No change to how Settings or Users behave for admins.
- No audit-log or events UI. Those endpoints keep their admin guard precisely
  because no page reads them yet.
