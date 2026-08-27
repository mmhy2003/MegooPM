# Auth & RBAC API contract (MEG-14)

Backend authentication, user management, and role-based access control. The
machine-readable contract is `backend/openapi.json` (consumed by the frontend's
`npm run gen:api`); this document is the human summary for Frontend and QA.

All application routes are versioned under `/api/v1`.

## Authentication model

- **JWT bearer tokens**, signed with HMAC (`HS256`) using `SECRET_KEY`.
- **Access token** — short-lived (default 30 min, `ACCESS_TOKEN_EXPIRE_MINUTES`).
  Sent on every request as `Authorization: Bearer <access_token>`. Carries the
  user's `role` claim.
- **Refresh token** — longer-lived (default 7 days,
  `REFRESH_TOKEN_EXPIRE_MINUTES`). Exchanged for a new token pair; **rotated** on
  each use. Not accepted where an access token is required (and vice versa —
  the `type` claim is enforced).
- Passwords are hashed with **Argon2id** (`argon2-cffi`); hashes are never
  returned by any endpoint.

## Roles (RBAC)

| Role     | Meaning                              |
| -------- | ------------------------------------ |
| `admin`  | May perform privileged actions.      |
| `member` | Limited user (default for new users).|

Admin-only endpoints return **403** for authenticated non-admins. Any protected
endpoint returns **401** when the token is missing, malformed, expired, of the
wrong type, or belongs to a deactivated/deleted user.

## Endpoints

### `POST /api/v1/auth/login`
Body: `{ "email": string, "password": string }` (email is case-insensitive).
→ `200 { "access_token", "refresh_token", "token_type": "bearer" }`
→ `401` on bad credentials or inactive account.

### `POST /api/v1/auth/refresh`
Body: `{ "refresh_token": string }`
→ `200` with a **new** access/refresh pair (role re-read from the DB).
→ `401` on an invalid/expired/non-refresh token.

### `GET /api/v1/auth/me`  _(auth required)_
→ `200` `UserRead` for the caller. → `401` if unauthenticated.

### `GET /api/v1/users/me`  _(auth required)_
Alias of `auth/me`, for symmetry with the users resource.

### `GET /api/v1/users`  _(admin only)_
→ `200` `UserRead[]`. → `401` unauth, `403` non-admin.

### `POST /api/v1/users`  _(admin only)_
Body: `UserCreate` = `{ email, password (min 8), full_name?, role?, is_active? }`
→ `201` `UserRead`. → `401`/`403` per RBAC, `409` if the email is taken.

### `PATCH /api/v1/users/me`  _(auth required)_
Body: `ProfileUpdate` = `{ full_name }` (nothing else is self-editable).
→ `200` `UserRead`. → `401` unauth, `422` on extra fields.

### `PUT /api/v1/users/me/password`  _(auth required)_
Body: `PasswordChange` = `{ current_password, new_password (min 8) }`
→ `204`. → `400 { "detail": "Current password is incorrect" }`, `401` unauth.

### `PATCH /api/v1/users/{id}`  _(admin only)_
Body: `UserUpdate` = `{ full_name?, role?, is_active? }` — at least one; `email`
is immutable and rejected with `422`.
→ `200` `UserRead`. → `404` unknown id, `409` under the lock-out rules below.

### `PUT /api/v1/users/{id}/password`  _(admin only)_
Body: `PasswordReset` = `{ password (min 8) }`. → `204`. → `404` unknown id.

### `DELETE /api/v1/users/{id}`  _(admin only)_
Hard delete. → `204`. → `404` unknown id, `409` under the lock-out rules.

## Lock-out rules (409)

Enforced in `app/services/user.py::assert_no_lockout` for every admin mutation:

1. You cannot change your own role, deactivate yourself, or delete yourself.
2. The last **active** admin cannot be demoted, deactivated, or deleted.

Effects are immediate: roles are read from the DB per request, and tokens of
inactive/deleted users are rejected with `401`.

## Audit

Every user mutation writes one `audit_log` row (`object_type="user"`):
`create` / `update` (with `meta.changes = {field: [before, after]}`) /
`enable` / `disable` (a lone `is_active` flip) / `delete` (`meta.email`,
`meta.role`). Password operations log `update` with
`{"password_reset": true}` or `{"password_changed": true}` — never a password.

### `UserRead` shape
`{ id, email, full_name, role, is_active, created_at, updated_at }` — no password.

## Bootstrapping the first admin

Creating users is admin-only, so the very first admin has to come from outside
the API. Two paths:

**Automatic (initial setup).** On startup the backend calls
`ensure_first_admin`: when `FIRST_ADMIN_EMAIL` and `FIRST_ADMIN_PASSWORD` are
set **and the users table is empty**, it creates that account as an active
`admin`. It never runs once any user exists, so renaming or deleting the seeded
account is permanent. The dev compose defaults these to
`admin@example.com` / `changeme` (and logs a warning when that well-known
password is seeded); the HA compose ships no default. Seeding is best-effort —
a failure is logged and the API still starts.

**Manual (CLI).** Same idempotent effect, on demand:

```bash
cd backend
python -m scripts.create_user --email admin@example.com --password '…' --role admin
# or, from FIRST_ADMIN_EMAIL / FIRST_ADMIN_PASSWORD:
python -m scripts.create_user --from-env
```

## Notes for Frontend

- Login POSTs JSON (not an OAuth2 form). On success, store both tokens; send the
  access token as a Bearer header; call `/auth/refresh` when it expires; treat a
  `401` from `/auth/me` as "logged out".
- `GET /auth/me` is the canonical "who am I" call for gating protected routes.

## Notes for QA (acceptance criteria)

- Login returns a JWT and refresh returns a fresh, usable pair.
- Protected endpoints reject unauthenticated requests with **401**.
- Admin-only actions (`GET`/`POST /users`) are denied to `member` with **403**.

Covered by `backend/tests/test_auth.py`, `test_users_rbac.py`, `test_security.py`.
