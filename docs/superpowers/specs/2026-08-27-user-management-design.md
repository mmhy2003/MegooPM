# User management with roles — design

Date: 2026-08-27 · Status: approved design, awaiting implementation plan

## Goal

Give admins a **Users** page to create, edit, reset passwords for, and delete
accounts with the existing `admin` / `member` roles, and give every signed-in
user an **Account** page to edit their display name and change their own
password. Today the backend exposes only `GET /users`, `POST /users` and
`GET /users/me`, and the only way to manage accounts is `scripts.create_user`.

## Non-goals

- New roles or a permissions model. `UserRole` stays `admin` | `member`.
- Changing a user's email (identity) after creation.
- Email-based flows (invitations, reset links). Admin password resets are handed
  over out of band.
- Soft delete. `DELETE` is a hard delete (chosen explicitly; audit rows survive
  because `audit_log` has no FK to `users`).
- DNS-01 provider support for certificates — a separate sub-project.

## Decisions taken during brainstorming

| Decision | Choice |
|---|---|
| Role model | Keep `admin` + `member` unchanged |
| Operations | Create/edit, admin password reset, hard delete, self-service password change |
| Shape | Approach B: dedicated password endpoints and an `/account` page |
| Guard failures | One `UserProtectionError` → HTTP **409 Conflict** with a specific message |
| Nav | `Users` is `adminOnly` in the primary sidebar; `/account` lives in the topbar account menu only |

## Backend

### Endpoints

All under `/api/v1/users`. `/me/*` routes are declared **before** `/{user_id}/*`
so they can never be shadowed by the integer path parameter.

| Method | Path | Access | Request → Response |
|---|---|---|---|
| `GET` | `/users` | admin | (exists) → `UserRead[]` |
| `POST` | `/users` | admin | (exists) `UserCreate` → `201 UserRead`; **now also writes an audit row** |
| `GET` | `/users/me` | any signed-in | (exists) → `UserRead` |
| `PATCH` | `/users/me` | any signed-in | `ProfileUpdate { full_name }` → `200 UserRead` |
| `PUT` | `/users/me/password` | any signed-in | `PasswordChange { current_password, new_password }` → `204`; wrong current password → `400` |
| `PATCH` | `/users/{id}` | admin | `UserUpdate { full_name?, role?, is_active? }` → `200 UserRead`; unknown → `404` |
| `PUT` | `/users/{id}/password` | admin | `PasswordReset { password }` → `204`; unknown → `404` |
| `DELETE` | `/users/{id}` | admin | → `204`; unknown → `404` |

Schemas (in `app/schemas/user.py`): `UserUpdate` (all fields optional, at least
one required, `extra="forbid"` so `email` is rejected rather than ignored),
`PasswordReset` and `PasswordChange` (new password `min_length=8,
max_length=128`, same as `UserCreate`), `ProfileUpdate`.

### Lock-out guards

Enforced in `app/services/user.py`, never in routes, so the CLI and any future
caller get the same protection. A single `UserProtectionError(message)` is
raised and the route maps it to **409** with the message as `detail`:

1. An actor may not change **their own** role, deactivate themselves, or delete
   themselves.
2. The **last active admin** (`role = admin AND is_active = true`, count = 1)
   may not be demoted, deactivated, or deleted.

`_assert_no_lockout(db, target, *, actor, new_role, new_active, deleting)`
implements both rules and is unit-tested directly.

Effects are immediate with no extra work: `require_admin` reads the role from
the DB on every request (not from the JWT claim), and `get_current_user` already
rejects tokens of inactive or missing users with 401.

### Service functions (new)

```
update_user(db, user, *, full_name=None, role=None, is_active=None, actor) -> User
set_password(db, user, password) -> None                # admin reset
change_own_password(db, user, current_password, new_password) -> None
    raises InvalidCurrentPasswordError
delete_user(db, user, *, actor) -> None
```

`update_user` returns the refreshed row and computes the before→after diff the
audit row records.

### Audit

Every mutation calls the existing `record_audit(session, actor=<admin or self
email>, action=..., object_type="user", object_id=<id>, meta=...)` inside the
same transaction:

| Mutation | `action` | `meta` |
|---|---|---|
| create | `create` | `{ "email", "role", "is_active" }` |
| update (any field but a lone `is_active` flip) | `update` | `{ "changes": { field: [before, after] } }` |
| update that only flips `is_active` | `enable` / `disable` | `{}` |
| admin password reset | `update` | `{ "password_reset": true }` |
| self password change | `update` | `{ "password_changed": true }` |
| delete | `delete` | `{ "email", "role" }` |

Passwords and hashes never appear in `meta`.

## Frontend

### Navigation and gating

- `NavItem` in `src/config/nav.ts` gains `adminOnly?: boolean`. New entry:
  `{ title: "Users", href: "/users", icon: Users, adminOnly: true }` appended
  to `primaryNav`.
- New pure helper `navForRole(role: UserRole | null | undefined): NavItem[]`
  in `nav.ts` filters `adminOnly` items unless `role === "admin"`. The sidebar
  calls it with `useAuth().user?.role`.
- With `NEXT_PUBLIC_AUTH_ENABLED=false` and no session, `user` is `null`, so
  the item is hidden; every other page already 401s in that state, so nothing
  new is exposed. A member who navigates to `/users` directly sees the page's
  standard error state rendering the API's 403.
- `/account` is not in the sidebar. The topbar account menu gains an
  **Account** item (above **Sign out**) linking to it. `nav.ts` exports
  `utilityRoutes: Record<string, string>` (`{ "/account": "Account" }`) and the
  topbar's `currentTitle` falls back to it after `primaryNav`.

### API client

`src/lib/api/resources/users.ts`, modelled on `certificates.ts`; every type
derived from the regenerated schema (`UserRead`, `UserCreate`, `UserUpdate`,
`PasswordReset`, `PasswordChange`, `ProfileUpdate`, `UserRole`):

```
users.list()                      GET    /api/v1/users
users.create(body)                POST   /api/v1/users
users.update(id, body)            PATCH  /api/v1/users/{id}
users.resetPassword(id, body)     PUT    /api/v1/users/{id}/password
users.remove(id)                  DELETE /api/v1/users/{id}
users.updateMe(body)              PATCH  /api/v1/users/me
users.changeMyPassword(body)      PUT    /api/v1/users/me/password
USER_ROLE_LABELS: Record<UserRole, string>
```

Re-exported from `src/lib/api/index.ts`.

### `/users` page

`src/app/(app)/users/page.tsx` → `src/components/users/users-view.tsx`, same
skeleton as `proxy-hosts-view.tsx` (load on mount, skeleton rows, error alert
with `role="alert"`, empty state, refetch after each mutation).

Table columns: Name · Email · Role (badge) · Status (Active/Inactive badge) ·
Created · Actions (edit, reset password, delete). The signed-in user's own row
shows a **You** badge and has the role select, active switch, and delete action
disabled client-side (pure helpers in `components/users/lib.ts`); the server's
409 guards remain the enforcement and any 409 is shown in the dialog's alert
through the existing `describeError`.

Dialogs (in `src/components/users/`):

- `user-dialog.tsx` — create mode: email, full name, role, active, password;
  edit mode: email read-only, full name, role, active.
- `reset-password-dialog.tsx` — new password + confirm (client-side match
  check).
- Delete uses the existing `ConfirmDeleteDialog` from
  `components/proxy-hosts/` (cross-area import is the established pattern —
  `certificate-dialog.tsx` already imports `describeError` the same way).

### `/account` page

`src/app/(app)/account/page.tsx` → `src/components/account/account-view.tsx`
with two cards:

- **Profile** — email (read-only), full name → `users.updateMe`. On success the
  topbar label must update, so `AuthContextValue` gains
  `refreshUser(): Promise<void>` (re-fetches `/users/me` and sets `user`). This
  is the only change to shared auth code.
- **Password** — current, new, confirm → `users.changeMyPassword`. Mismatch is
  caught client-side; a 400 from the API renders inline as "Current password is
  incorrect".

## Testing

### Backend (`backend/tests/test_users_management.py`, pytest, Linux container)

Uses the existing `db_client`, `admin_user`, `member_user`, `admin_token`,
`member_token` fixtures.

- `PATCH /users/{id}`: admin updates name/role/active → 200 with new values;
  member → 403; unknown id → 404; body containing `email` → 422.
- Guards: self role change / self deactivate / self delete → 409; last-admin
  demote / deactivate / delete → 409; each succeeds once a second active admin
  exists.
- Password reset: → 204; login succeeds with the new password and fails with
  the old; member → 403; unknown id → 404.
- `DELETE`: → 204; user absent from the list; the deleted user's existing token
  → 401.
- Self-service: `PATCH /users/me` changes `full_name` only; `PUT
  /users/me/password` wrong current → 400, correct → 204 then login with the
  new password works; both → 401 unauthenticated.
- Audit: each mutation writes exactly one `audit_log` row with the expected
  `actor`, `action`, `object_type="user"`, `object_id`; password rows never
  contain the password.
- `_assert_no_lockout` unit tests via `session_factory` (one admin, two admins,
  inactive second admin does not count).

### Frontend (vitest + testing-library)

- `config/nav.test.ts`: title list includes "Users"; `navForRole("admin")`
  includes it, `navForRole("member")` and `navForRole(null)` exclude it.
- `components/users/lib.test.ts`: own-row detection and disabled-control
  helpers.
- `components/users/users-view.test.tsx`: renders rows from a mocked
  `users.list`; own row shows "You" with disabled destructive controls; delete
  confirmation triggers a refetch (dialogs mocked as in
  `security-view.test.tsx`).
- `components/account/account-view.test.tsx`: confirm mismatch blocks submit
  without an API call; a rejected `changeMyPassword` (400) renders the inline
  error.

### Gates (must stay green)

Backend: `ruff check .`, `ruff format --check` on touched files, `alembic
check` (no migration in this change), `pytest`. Frontend: `npm run lint`,
`npm run typecheck`, `npm run test`, `npm run build`, and the CI "generated API
types are in sync" step.

## Contract and docs

- After the backend lands: `python -m scripts.export_openapi` (from
  `backend/`) then `npm run gen:api`; commit `backend/openapi.json` and
  `frontend/src/lib/api/generated/schema.ts` with the change.
- `docs/auth-api.md`: document the five new endpoints, the 409 guard rules, and
  the audit behaviour.
- `docs/CONVENTIONS.md`: note `adminOnly` nav items, `navForRole`, and
  `utilityRoutes` for account-menu pages.
- `docs/backlog/audit-log-endpoints.md`: users are now covered by the write
  path.
- `README.md`: one line under the default-login paragraph pointing admins at
  `/users`.

## Live verification (compose stack)

1. Sign in as `admin@example.com`; the sidebar shows **Users**; the page lists
   the seeded admin with a **You** badge and disabled destructive controls.
2. Create a member; reset its password; sign in as the member: **Users** is
   hidden and `/users` shows the 403 error state.
3. As the member, change the password on `/account`; sign out and back in with
   the new password.
4. As the admin, attempt to delete yourself → 409 message in the dialog; delete
   the member → row gone; the audit log shows the rows.
5. Headless screenshot of `/users` for the record.

## Files touched

Backend: `app/schemas/user.py`, `app/services/user.py`,
`app/api/routes/users.py`, `tests/test_users_management.py` (new),
`openapi.json` (regenerated).

Frontend: `src/config/nav.ts` + test, `src/components/app-sidebar.tsx`,
`src/components/app-topbar.tsx`, `src/lib/auth/context.tsx`,
`src/lib/api/resources/users.ts` (new), `src/lib/api/index.ts`,
`src/app/(app)/users/page.tsx` (new), `src/app/(app)/account/page.tsx` (new),
`src/components/users/{users-view,user-dialog,reset-password-dialog,lib}.tsx|ts`
(new, + tests), `src/components/account/account-view.tsx` (new, + test),
`src/lib/api/generated/schema.ts` (regenerated).

Docs: `docs/auth-api.md`, `docs/CONVENTIONS.md`,
`docs/backlog/audit-log-endpoints.md`, `README.md`.
