# User Management with Roles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give admins a `/users` page to create, edit, reset passwords for, and delete accounts (roles `admin` / `member`), and give every signed-in user an `/account` page to edit their display name and change their own password.

**Architecture:** Five new FastAPI routes on the existing `/api/v1/users` router, with lock-out guards and password logic in `app/services/user.py` (routes stay thin and write audit rows via the existing `record_audit`). The frontend adds a typed `users` API resource, an admin-only `Users` nav entry filtered by role, a `users-view` table with create/edit/reset/delete dialogs, and an `account-view` with profile + password cards; the auth context gains `refreshUser()` so the topbar label updates after a profile edit.

**Tech Stack:** FastAPI + SQLAlchemy async + Pydantic v2 (backend, Python 3.12 in Docker), pytest + pytest-asyncio (SQLite in-memory fixtures), Next.js 16 App Router + React 19 + TypeScript strict, shadcn/base-ui components, vitest + @testing-library/react, openapi-typescript for generated types.

**Spec:** `docs/superpowers/specs/2026-08-27-user-management-design.md`

## Global Constraints

- Roles stay exactly `admin` | `member` (`app/models/user.py::UserRole`). No migration in this change; `alembic check` must stay green.
- Email is immutable after creation; `UserUpdate` uses `extra="forbid"` so a body containing `email` is a 422.
- Every lock-out violation is a single `UserProtectionError` → HTTP **409** with the message as `detail`.
- Passwords: `min_length=8, max_length=128` (same as `UserCreate`). Passwords/hashes never appear in audit `meta`.
- `/users/me` and `/users/me/password` routes are declared **before** any `/users/{user_id}` route in `app/api/routes/users.py`.
- Never hand-edit `backend/openapi.json` or `frontend/src/lib/api/generated/schema.ts`; regenerate them (Task 5) and commit them.
- Frontend types for users come from `Schemas[...]` in `@/lib/api/types` — no hand-authored request/response shapes.
- Next.js 16 conventions apply (see `frontend/AGENTS.md`); do not use `middleware.ts` or pre-16 patterns.
- **Line endings:** this Windows checkout's editor tooling sometimes writes CRLF. Before every commit run `sed -i 's/\r$//' <files you touched>` and confirm `git ls-files --eol <files>` shows `w/lf`.
- **Backend tests run in Linux only** (the app imports `fcntl`). One-time setup, from the repo root in Git Bash:
  ```bash
  export MSYS_NO_PATHCONV=1
  docker run -d --name megoopm-test --user root -v "C:/Projects/megoopm/backend:/src" -w /src \
    -e CELERY_TASK_ALWAYS_EAGER=true -e CELERY_RESULT_BACKEND=cache+memory:// \
    --entrypoint sleep megoopm-backend infinity
  docker exec megoopm-test pip install -q --root-user-action=ignore "pytest>=8.2" "pytest-asyncio>=0.23" "aiosqlite>=0.20" "ruff>=0.6"
  ```
  Then every backend command in this plan is `docker exec megoopm-test <cmd>` (the bind mount means edits on the host are visible immediately, and files the container writes — e.g. `openapi.json` — land on the host). `pyproject.toml` already sets `addopts = "-q"`; **do not pass `-q` again** or the summary line disappears. Remove the container at the end: `docker rm -f megoopm-test`.
- Ruff enforces `line-length = 100` (`E501`). Always run `ruff format <files>` **before** `ruff check <files>` — the code blocks in this plan contain a few call sites longer than 100 columns that the formatter wraps; checking first would fail on them.
- **Frontend commands run on the host** (Node 22): once, `cd frontend && npm ci`. Then `npx vitest run <path>`, `npm run lint`, `npm run typecheck`, `npm run gen:api`, `npm run build` from `frontend/`.
- Commit after every task with a Conventional-Commits subject and the trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. Never `--no-verify`.

---

## File structure

| File | Responsibility |
|---|---|
| `backend/app/schemas/user.py` | + `UserUpdate`, `PasswordReset`, `PasswordChange`, `ProfileUpdate` |
| `backend/app/services/user.py` | + `UserProtectionError`, `InvalidCurrentPasswordError`, `count_active_admins`, `assert_no_lockout`, `update_user`, `set_password`, `change_own_password`, `delete_user` |
| `backend/app/api/routes/users.py` | + 5 routes, audit on every mutation (incl. existing create), 404/409 mapping |
| `backend/tests/test_user_schemas.py` | schema validation tests |
| `backend/tests/test_user_service.py` | guard / password / delete service tests |
| `backend/tests/test_users_management.py` | route tests (RBAC, guards, audit) |
| `backend/openapi.json`, `frontend/src/lib/api/generated/schema.ts` | regenerated contract |
| `frontend/src/config/nav.ts` (+ `nav.test.ts`) | `adminOnly`, `navForRole`, `utilityRoutes` |
| `frontend/src/components/app-sidebar.tsx` | role-filtered nav |
| `frontend/src/components/app-topbar.tsx` | title fallback for utility routes; Account menu item |
| `frontend/src/lib/api/resources/users.ts` (+ `users.test.ts`), `frontend/src/lib/api/index.ts` | typed `users` resource |
| `frontend/src/components/users/lib.ts` (+ `lib.test.ts`) | pure helpers: `displayName`, `isSelf`, `validateNewPassword` |
| `frontend/src/components/users/user-dialog.tsx` | create / edit dialog |
| `frontend/src/components/users/reset-password-dialog.tsx` | admin password reset dialog |
| `frontend/src/components/users/users-view.tsx` (+ `users-view.test.tsx`), `frontend/src/app/(app)/users/page.tsx` | Users page |
| `frontend/src/lib/auth/context.tsx` | + `refreshUser()` |
| `frontend/src/components/account/account-view.tsx` (+ `account-view.test.tsx`), `frontend/src/app/(app)/account/page.tsx` | Account page |
| `docs/auth-api.md`, `docs/CONVENTIONS.md`, `docs/backlog/audit-log-endpoints.md`, `README.md` | docs |

---

### Task 1: Request schemas

**Files:**
- Modify: `backend/app/schemas/user.py` (append after `UserRead`, update `__all__`)
- Test: `backend/tests/test_user_schemas.py` (new)

**Interfaces:**
- Consumes: `UserRole` from `app.models.user`.
- Produces: `UserUpdate(full_name: str | None, role: UserRole | None, is_active: bool | None)` (extra fields forbidden, at least one field required), `PasswordReset(password: str)`, `PasswordChange(current_password: str, new_password: str)`, `ProfileUpdate(full_name: str)` (extra forbidden). Tasks 3–4 import these.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_user_schemas.py`:

```python
"""Validation rules for the user-management request schemas."""

from __future__ import annotations

import pytest
from app.schemas.user import PasswordChange, PasswordReset, ProfileUpdate, UserUpdate
from pydantic import ValidationError


def test_user_update_rejects_email() -> None:
    with pytest.raises(ValidationError):
        UserUpdate(email="new@example.com", full_name="x")  # type: ignore[call-arg]


def test_user_update_requires_at_least_one_field() -> None:
    with pytest.raises(ValidationError):
        UserUpdate()


def test_user_update_accepts_a_single_field() -> None:
    body = UserUpdate(role="admin")
    assert body.role == "admin"
    assert body.full_name is None
    assert body.is_active is None


def test_password_reset_enforces_min_length() -> None:
    with pytest.raises(ValidationError):
        PasswordReset(password="short")
    assert PasswordReset(password="longenough").password == "longenough"


def test_password_change_enforces_new_password_min_length_only() -> None:
    with pytest.raises(ValidationError):
        PasswordChange(current_password="whatever", new_password="short")
    body = PasswordChange(current_password="x", new_password="longenough")
    assert body.current_password == "x"


def test_profile_update_rejects_role_changes() -> None:
    with pytest.raises(ValidationError):
        ProfileUpdate(full_name="Me", role="admin")  # type: ignore[call-arg]
    assert ProfileUpdate(full_name="Me").full_name == "Me"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker exec megoopm-test python -m pytest tests/test_user_schemas.py -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'PasswordChange' from 'app.schemas.user'`.

- [ ] **Step 3: Add the schemas**

In `backend/app/schemas/user.py`, change the import line to:

```python
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator
```

Append after `UserRead`:

```python
class UserUpdate(BaseModel):
    """Admin partial update of another user.

    ``email`` is identity and immutable, so it is *rejected* (``extra="forbid"``)
    rather than silently ignored. At least one field must be present.
    """

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, max_length=255)
    role: UserRole | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _require_a_field(self) -> UserUpdate:
        if self.full_name is None and self.role is None and self.is_active is None:
            raise ValueError("Provide at least one of full_name, role, is_active.")
        return self


class PasswordReset(BaseModel):
    """Admin-set password for another user (handed over out of band)."""

    password: str = Field(min_length=8, max_length=128)


class PasswordChange(BaseModel):
    """Self-service password change; the current password is re-verified."""

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class ProfileUpdate(BaseModel):
    """Self-service profile edit. Only the display name is user-editable."""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(max_length=255)
```

Replace `__all__` with:

```python
__all__ = [
    "PasswordChange",
    "PasswordReset",
    "ProfileUpdate",
    "UserBase",
    "UserCreate",
    "UserRead",
    "UserUpdate",
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker exec megoopm-test python -m pytest tests/test_user_schemas.py -p no:cacheprovider`
Expected: `6 passed`.

- [ ] **Step 5: Lint, normalize, commit**

```bash
docker exec megoopm-test ruff format app/schemas/user.py tests/test_user_schemas.py
docker exec megoopm-test ruff check app/schemas/user.py tests/test_user_schemas.py
sed -i 's/\r$//' backend/app/schemas/user.py backend/tests/test_user_schemas.py
git add backend/app/schemas/user.py backend/tests/test_user_schemas.py
git commit -m "feat(users): request schemas for update, password reset/change, profile

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Service layer — guards, updates, passwords, delete

**Files:**
- Modify: `backend/app/services/user.py` (imports at lines 11–15; append new functions after `authenticate`; update `__all__`)
- Test: `backend/tests/test_user_service.py` (new)

**Interfaces:**
- Consumes: `create_user`, `verify_password`, `hash_password` (already in the module), `session_factory` fixture from `tests/conftest.py`.
- Produces (all `async`, first arg `db: AsyncSession`):
  - `class UserProtectionError(Exception)` — `str(exc)` is the user-facing message.
  - `class InvalidCurrentPasswordError(Exception)`.
  - `count_active_admins(db) -> int`
  - `assert_no_lockout(db, target: User, *, actor: User, new_role: UserRole | None = None, new_active: bool | None = None, deleting: bool = False) -> None`
  - `update_user(db, user: User, *, actor: User, full_name: str | None = None, role: UserRole | None = None, is_active: bool | None = None) -> tuple[User, dict[str, list[object]]]` — second element is `{field: [before, after]}` for fields that actually changed (role values as strings).
  - `set_password(db, user: User, password: str) -> None`
  - `change_own_password(db, user: User, *, current_password: str, new_password: str) -> None`
  - `delete_user(db, user: User, *, actor: User) -> None`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_user_service.py`:

```python
"""Service-level behaviour for user management: lock-out guards, passwords, delete."""

from __future__ import annotations

import pytest
from app.models.user import User, UserRole
from app.services import user as user_service
from sqlalchemy.ext.asyncio import async_sessionmaker


async def _make(session_factory: async_sessionmaker, email: str, role: UserRole, *, active=True) -> User:
    async with session_factory() as session:
        return await user_service.create_user(
            session, email=email, password="password123", role=role, is_active=active
        )


# --- count_active_admins ---------------------------------------------------


async def test_count_active_admins_ignores_members_and_inactive_admins(session_factory):
    await _make(session_factory, "a1@example.com", UserRole.admin)
    await _make(session_factory, "a2@example.com", UserRole.admin, active=False)
    await _make(session_factory, "m@example.com", UserRole.member)
    async with session_factory() as session:
        assert await user_service.count_active_admins(session) == 1


# --- assert_no_lockout -----------------------------------------------------


async def test_actor_cannot_change_own_role(session_factory):
    me = await _make(session_factory, "me@example.com", UserRole.admin)
    await _make(session_factory, "other@example.com", UserRole.admin)
    async with session_factory() as session:
        with pytest.raises(user_service.UserProtectionError, match="own role"):
            await user_service.assert_no_lockout(session, me, actor=me, new_role=UserRole.member)


async def test_actor_setting_own_role_to_same_value_is_allowed(session_factory):
    me = await _make(session_factory, "me@example.com", UserRole.admin)
    async with session_factory() as session:
        await user_service.assert_no_lockout(session, me, actor=me, new_role=UserRole.admin)


async def test_actor_cannot_deactivate_or_delete_self(session_factory):
    me = await _make(session_factory, "me@example.com", UserRole.admin)
    await _make(session_factory, "other@example.com", UserRole.admin)
    async with session_factory() as session:
        with pytest.raises(user_service.UserProtectionError, match="deactivate your own"):
            await user_service.assert_no_lockout(session, me, actor=me, new_active=False)
        with pytest.raises(user_service.UserProtectionError, match="delete your own"):
            await user_service.assert_no_lockout(session, me, actor=me, deleting=True)


async def test_last_active_admin_is_protected_from_another_actor(session_factory):
    only_admin = await _make(session_factory, "only@example.com", UserRole.admin)
    # A second, *inactive* admin does not count.
    await _make(session_factory, "sleeping@example.com", UserRole.admin, active=False)
    actor = await _make(session_factory, "m@example.com", UserRole.member)
    async with session_factory() as session:
        for kwargs in ({"new_role": UserRole.member}, {"new_active": False}, {"deleting": True}):
            with pytest.raises(user_service.UserProtectionError, match="last active admin"):
                await user_service.assert_no_lockout(session, only_admin, actor=actor, **kwargs)


async def test_admin_can_be_demoted_when_another_active_admin_exists(session_factory):
    a = await _make(session_factory, "a@example.com", UserRole.admin)
    b = await _make(session_factory, "b@example.com", UserRole.admin)
    async with session_factory() as session:
        await user_service.assert_no_lockout(session, b, actor=a, new_role=UserRole.member)
        await user_service.assert_no_lockout(session, b, actor=a, new_active=False)
        await user_service.assert_no_lockout(session, b, actor=a, deleting=True)


# --- update_user -----------------------------------------------------------


async def test_update_user_applies_fields_and_reports_changes(session_factory):
    a = await _make(session_factory, "a@example.com", UserRole.admin)
    m = await _make(session_factory, "m@example.com", UserRole.member)
    async with session_factory() as session:
        m = await user_service.get_by_id(session, m.id)
        updated, changes = await user_service.update_user(
            session, m, actor=a, full_name="Mem Ber", role=UserRole.admin
        )
        assert updated.full_name == "Mem Ber"
        assert updated.role == UserRole.admin
        assert changes == {"full_name": ["", "Mem Ber"], "role": ["member", "admin"]}


async def test_update_user_reports_no_changes_for_identical_values(session_factory):
    a = await _make(session_factory, "a@example.com", UserRole.admin)
    m = await _make(session_factory, "m@example.com", UserRole.member)
    async with session_factory() as session:
        m = await user_service.get_by_id(session, m.id)
        _, changes = await user_service.update_user(session, m, actor=a, role=UserRole.member)
        assert changes == {}


async def test_update_user_enforces_guards(session_factory):
    a = await _make(session_factory, "a@example.com", UserRole.admin)
    async with session_factory() as session:
        a = await user_service.get_by_id(session, a.id)
        with pytest.raises(user_service.UserProtectionError):
            await user_service.update_user(session, a, actor=a, is_active=False)


# --- passwords -------------------------------------------------------------


async def test_set_password_replaces_credentials(session_factory):
    m = await _make(session_factory, "m@example.com", UserRole.member)
    async with session_factory() as session:
        m = await user_service.get_by_id(session, m.id)
        await user_service.set_password(session, m, "brandnew123")
    async with session_factory() as session:
        assert await user_service.authenticate(session, email="m@example.com", password="brandnew123")
        assert not await user_service.authenticate(session, email="m@example.com", password="password123")


async def test_change_own_password_requires_correct_current_password(session_factory):
    m = await _make(session_factory, "m@example.com", UserRole.member)
    async with session_factory() as session:
        m = await user_service.get_by_id(session, m.id)
        with pytest.raises(user_service.InvalidCurrentPasswordError):
            await user_service.change_own_password(
                session, m, current_password="wrong", new_password="brandnew123"
            )
        await user_service.change_own_password(
            session, m, current_password="password123", new_password="brandnew123"
        )
    async with session_factory() as session:
        assert await user_service.authenticate(session, email="m@example.com", password="brandnew123")


# --- delete ----------------------------------------------------------------


async def test_delete_user_removes_row(session_factory):
    a = await _make(session_factory, "a@example.com", UserRole.admin)
    m = await _make(session_factory, "m@example.com", UserRole.member)
    async with session_factory() as session:
        m = await user_service.get_by_id(session, m.id)
        await user_service.delete_user(session, m, actor=a)
        assert await user_service.get_by_email(session, "m@example.com") is None


async def test_delete_user_enforces_guards(session_factory):
    a = await _make(session_factory, "a@example.com", UserRole.admin)
    async with session_factory() as session:
        a = await user_service.get_by_id(session, a.id)
        with pytest.raises(user_service.UserProtectionError):
            await user_service.delete_user(session, a, actor=a)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker exec megoopm-test python -m pytest tests/test_user_service.py -p no:cacheprovider`
Expected: FAIL — `AttributeError: module 'app.services.user' has no attribute 'count_active_admins'` (and similar for each new name).

- [ ] **Step 3: Implement the service functions**

In `backend/app/services/user.py` change the SQLAlchemy import to:

```python
from sqlalchemy import func, select
```

Add after `EmailAlreadyExistsError`:

```python
class UserProtectionError(Exception):
    """A mutation would lock the actor out or leave the system without an admin.

    ``str(exc)`` is the user-facing message; routes map this to HTTP 409.
    """


class InvalidCurrentPasswordError(Exception):
    """Self-service password change supplied the wrong current password."""
```

Append after `authenticate` (before `__all__`):

```python
async def count_active_admins(db: AsyncSession) -> int:
    """Number of users with ``role=admin`` and ``is_active=True``."""
    result = await db.execute(
        select(func.count())
        .select_from(User)
        .where(User.role == UserRole.admin, User.is_active.is_(True))
    )
    return int(result.scalar_one())


async def assert_no_lockout(
    db: AsyncSession,
    target: User,
    *,
    actor: User,
    new_role: UserRole | None = None,
    new_active: bool | None = None,
    deleting: bool = False,
) -> None:
    """Raise :class:`UserProtectionError` if a change would lock someone out.

    Two rules, checked in order:

    1. An actor may not change their own role, deactivate themselves, or
       delete themselves (an admin would otherwise strand their own session).
    2. The last *active* admin may not be demoted, deactivated, or deleted, so
       the system always keeps at least one account that can manage users.
    """
    if target.id == actor.id:
        if deleting:
            raise UserProtectionError("You cannot delete your own account.")
        if new_role is not None and new_role != target.role:
            raise UserProtectionError("You cannot change your own role.")
        if new_active is False:
            raise UserProtectionError("You cannot deactivate your own account.")

    target_is_active_admin = target.role == UserRole.admin and target.is_active
    loses_admin = (
        deleting
        or (new_role is not None and new_role != UserRole.admin)
        or new_active is False
    )
    if target_is_active_admin and loses_admin and await count_active_admins(db) <= 1:
        raise UserProtectionError(
            "Cannot remove the last active admin. Promote another user first."
        )


async def update_user(
    db: AsyncSession,
    user: User,
    *,
    actor: User,
    full_name: str | None = None,
    role: UserRole | None = None,
    is_active: bool | None = None,
) -> tuple[User, dict[str, list[object]]]:
    """Apply an admin partial update and return ``(user, changes)``.

    ``changes`` maps each field that actually changed to ``[before, after]``
    (roles as their string values) — the shape the audit row records. Raises
    :class:`UserProtectionError` before touching anything.
    """
    await assert_no_lockout(db, user, actor=actor, new_role=role, new_active=is_active)

    changes: dict[str, list[object]] = {}
    if full_name is not None and full_name != user.full_name:
        changes["full_name"] = [user.full_name, full_name]
        user.full_name = full_name
    if role is not None and role != user.role:
        changes["role"] = [user.role.value, role.value]
        user.role = role
    if is_active is not None and is_active != user.is_active:
        changes["is_active"] = [user.is_active, is_active]
        user.is_active = is_active

    if changes:
        await db.commit()
        await db.refresh(user)
    return user, changes


async def set_password(db: AsyncSession, user: User, password: str) -> None:
    """Replace ``user``'s password (admin reset — no current-password check)."""
    user.hashed_password = hash_password(password)
    await db.commit()


async def change_own_password(
    db: AsyncSession, user: User, *, current_password: str, new_password: str
) -> None:
    """Self-service change; raises :class:`InvalidCurrentPasswordError` on mismatch."""
    if not verify_password(current_password, user.hashed_password):
        raise InvalidCurrentPasswordError()
    user.hashed_password = hash_password(new_password)
    await db.commit()


async def delete_user(db: AsyncSession, user: User, *, actor: User) -> None:
    """Hard-delete ``user`` after the lock-out guards pass."""
    await assert_no_lockout(db, user, actor=actor, deleting=True)
    await db.delete(user)
    await db.commit()
```

Replace `__all__` with:

```python
__all__ = [
    "EmailAlreadyExistsError",
    "InvalidCurrentPasswordError",
    "UserProtectionError",
    "assert_no_lockout",
    "authenticate",
    "change_own_password",
    "count_active_admins",
    "create_user",
    "delete_user",
    "ensure_first_admin",
    "get_by_email",
    "get_by_id",
    "list_users",
    "set_password",
    "update_user",
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker exec megoopm-test python -m pytest tests/test_user_service.py tests/test_first_admin.py tests/test_users_rbac.py -p no:cacheprovider`
Expected: all pass (`13 passed` for the new file; the other two files unchanged).

- [ ] **Step 5: Lint, normalize, commit**

```bash
docker exec megoopm-test ruff format app/services/user.py tests/test_user_service.py
docker exec megoopm-test ruff check app/services/user.py tests/test_user_service.py
sed -i 's/\r$//' backend/app/services/user.py backend/tests/test_user_service.py
git add backend/app/services/user.py backend/tests/test_user_service.py
git commit -m "feat(users): lock-out guards, update/delete and password services

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Admin routes — PATCH / PUT password / DELETE, audit on every admin mutation

**Files:**
- Modify: `backend/app/api/routes/users.py` (whole file rewritten below)
- Test: `backend/tests/test_users_management.py` (new)

**Interfaces:**
- Consumes: Task 1 schemas; Task 2 service functions; `record_audit` from `app.services.audit`; `AuditAction` from `app.models.enums`; `AdminUser`, `CurrentUser`, `SessionDep` from `app.api.deps`.
- Produces: `PATCH /api/v1/users/{user_id}` → `200 UserRead` | `404` | `409`; `PUT /api/v1/users/{user_id}/password` → `204` | `404`; `DELETE /api/v1/users/{user_id}` → `204` | `404` | `409`. Module-private helpers `_get_or_404`, `_audit`, `_action_for` reused by Task 4.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_users_management.py`:

```python
"""Route tests for admin user management: update, password reset, delete, audit."""

from __future__ import annotations

from app.models.audit_log import AuditLog
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

USERS = "/api/v1/users"
ME = "/api/v1/users/me"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(client: AsyncClient, email: str, password: str) -> str:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _create(client: AsyncClient, admin_token: str, email: str, role: str = "member") -> dict:
    resp = await client.post(
        USERS,
        headers=_auth(admin_token),
        json={"email": email, "password": "password123", "role": role, "full_name": ""},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _audit_rows(session_factory: async_sessionmaker) -> list[AuditLog]:
    async with session_factory() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.object_type == "user").order_by(AuditLog.id)
        )
        return list(result.scalars().all())


# --- PATCH /users/{id} ------------------------------------------------------


async def test_admin_updates_name_role_and_active(
    db_client: AsyncClient, admin_token: str
) -> None:
    target = await _create(db_client, admin_token, "t@example.com")
    resp = await db_client.patch(
        f"{USERS}/{target['id']}",
        headers=_auth(admin_token),
        json={"full_name": "Tee", "role": "admin", "is_active": False},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["full_name"], body["role"], body["is_active"]) == ("Tee", "admin", False)


async def test_update_denied_to_member_and_unauthenticated(
    db_client: AsyncClient, admin_token: str, member_token: str
) -> None:
    target = await _create(db_client, admin_token, "t@example.com")
    assert (await db_client.patch(f"{USERS}/{target['id']}", json={"full_name": "x"})).status_code == 401
    resp = await db_client.patch(
        f"{USERS}/{target['id']}", headers=_auth(member_token), json={"full_name": "x"}
    )
    assert resp.status_code == 403


async def test_update_unknown_user_is_404(db_client: AsyncClient, admin_token: str) -> None:
    resp = await db_client.patch(f"{USERS}/999999", headers=_auth(admin_token), json={"full_name": "x"})
    assert resp.status_code == 404


async def test_update_rejects_email_change(db_client: AsyncClient, admin_token: str) -> None:
    target = await _create(db_client, admin_token, "t@example.com")
    resp = await db_client.patch(
        f"{USERS}/{target['id']}", headers=_auth(admin_token), json={"email": "new@example.com"}
    )
    assert resp.status_code == 422


async def test_self_role_change_deactivate_and_delete_are_409(
    db_client: AsyncClient, admin_token: str
) -> None:
    me = (await db_client.get(ME, headers=_auth(admin_token))).json()
    # A second admin exists, so only the *self* rule can be what trips.
    await _create(db_client, admin_token, "other@example.com", role="admin")

    r1 = await db_client.patch(f"{USERS}/{me['id']}", headers=_auth(admin_token), json={"role": "member"})
    r2 = await db_client.patch(f"{USERS}/{me['id']}", headers=_auth(admin_token), json={"is_active": False})
    r3 = await db_client.delete(f"{USERS}/{me['id']}", headers=_auth(admin_token))
    assert (r1.status_code, r2.status_code, r3.status_code) == (409, 409, 409)
    assert "own role" in r1.json()["detail"]


async def test_admin_handover_between_two_admins(
    db_client: AsyncClient, admin_token: str, admin_user
) -> None:
    """With two active admins, either may demote/delete the other; the survivor
    is then protected. (Through the API the last-admin rule can only ever be
    hit as a self-action — any *other* actor must itself be an active admin —
    so the pure "other actor" case is covered in test_user_service.py.)"""
    second = await _create(db_client, admin_token, "second@example.com", role="admin")
    second_token = await _login(db_client, "second@example.com", "password123")

    # Two active admins: `second` may demote the original...
    demote = await db_client.patch(
        f"{USERS}/{admin_user.id}", headers=_auth(second_token), json={"role": "member"}
    )
    assert demote.status_code == 200, demote.text
    # ...after which the original (now a member) is locked out of admin routes.
    assert (await db_client.get(USERS, headers=_auth(admin_token))).status_code == 403

    # `second` promotes the original back, and the original deletes `second`.
    promote = await db_client.patch(
        f"{USERS}/{admin_user.id}", headers=_auth(second_token), json={"role": "admin"}
    )
    assert promote.status_code == 200, promote.text
    gone = await db_client.delete(f"{USERS}/{second['id']}", headers=_auth(admin_token))
    assert gone.status_code == 204

    # The original is now the only active admin and cannot remove itself.
    resp = await db_client.patch(
        f"{USERS}/{admin_user.id}", headers=_auth(admin_token), json={"is_active": False}
    )
    assert resp.status_code == 409
    assert "own account" in resp.json()["detail"]


async def test_update_writes_audit_row_with_diff(
    db_client: AsyncClient, admin_token: str, session_factory: async_sessionmaker
) -> None:
    target = await _create(db_client, admin_token, "t@example.com")
    await db_client.patch(
        f"{USERS}/{target['id']}", headers=_auth(admin_token), json={"full_name": "Tee", "role": "admin"}
    )
    rows = await _audit_rows(session_factory)
    update = [r for r in rows if r.action == "update"]
    assert len(update) == 1
    assert update[0].actor == "admin@example.com"
    assert update[0].object_id == target["id"]
    assert update[0].meta == {"changes": {"full_name": ["", "Tee"], "role": ["member", "admin"]}}


async def test_active_flip_audits_as_enable_disable(
    db_client: AsyncClient, admin_token: str, session_factory: async_sessionmaker
) -> None:
    target = await _create(db_client, admin_token, "t@example.com")
    await db_client.patch(f"{USERS}/{target['id']}", headers=_auth(admin_token), json={"is_active": False})
    await db_client.patch(f"{USERS}/{target['id']}", headers=_auth(admin_token), json={"is_active": True})
    actions = [r.action for r in await _audit_rows(session_factory)]
    assert actions == ["create", "disable", "enable"]


async def test_create_writes_audit_row(
    db_client: AsyncClient, admin_token: str, session_factory: async_sessionmaker
) -> None:
    target = await _create(db_client, admin_token, "t@example.com", role="admin")
    rows = await _audit_rows(session_factory)
    assert len(rows) == 1
    assert rows[0].action == "create"
    assert rows[0].object_id == target["id"]
    assert rows[0].meta == {"email": "t@example.com", "role": "admin", "is_active": True}


# --- PUT /users/{id}/password ----------------------------------------------


async def test_admin_resets_password(
    db_client: AsyncClient, admin_token: str, session_factory: async_sessionmaker
) -> None:
    target = await _create(db_client, admin_token, "t@example.com")
    resp = await db_client.put(
        f"{USERS}/{target['id']}/password", headers=_auth(admin_token), json={"password": "brandnew123"}
    )
    assert resp.status_code == 204, resp.text
    assert await _login(db_client, "t@example.com", "brandnew123")
    old = await db_client.post("/api/v1/auth/login", json={"email": "t@example.com", "password": "password123"})
    assert old.status_code == 401
    rows = [r for r in await _audit_rows(session_factory) if r.action == "update"]
    assert rows[-1].meta == {"password_reset": True}
    assert "brandnew123" not in str(rows[-1].meta)


async def test_reset_password_denied_to_member_and_404_for_unknown(
    db_client: AsyncClient, admin_token: str, member_token: str
) -> None:
    target = await _create(db_client, admin_token, "t@example.com")
    denied = await db_client.put(
        f"{USERS}/{target['id']}/password", headers=_auth(member_token), json={"password": "brandnew123"}
    )
    assert denied.status_code == 403
    missing = await db_client.put(
        f"{USERS}/999999/password", headers=_auth(admin_token), json={"password": "brandnew123"}
    )
    assert missing.status_code == 404


# --- DELETE /users/{id} -----------------------------------------------------


async def test_admin_deletes_user_and_their_token_stops_working(
    db_client: AsyncClient, admin_token: str, session_factory: async_sessionmaker
) -> None:
    target = await _create(db_client, admin_token, "t@example.com")
    target_token = await _login(db_client, "t@example.com", "password123")

    resp = await db_client.delete(f"{USERS}/{target['id']}", headers=_auth(admin_token))
    assert resp.status_code == 204

    listed = (await db_client.get(USERS, headers=_auth(admin_token))).json()
    assert all(u["email"] != "t@example.com" for u in listed)
    assert (await db_client.get(ME, headers=_auth(target_token))).status_code == 401

    rows = await _audit_rows(session_factory)
    assert rows[-1].action == "delete"
    assert rows[-1].object_id == target["id"]
    assert rows[-1].meta == {"email": "t@example.com", "role": "member"}


async def test_delete_denied_to_member_and_404_for_unknown(
    db_client: AsyncClient, admin_token: str, member_token: str
) -> None:
    target = await _create(db_client, admin_token, "t@example.com")
    assert (await db_client.delete(f"{USERS}/{target['id']}", headers=_auth(member_token))).status_code == 403
    assert (await db_client.delete(f"{USERS}/999999", headers=_auth(admin_token))).status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker exec megoopm-test python -m pytest tests/test_users_management.py -p no:cacheprovider`
Expected: FAIL — the PATCH/PUT/DELETE tests get `405 Method Not Allowed`; `test_create_writes_audit_row` fails with `assert 0 == 1`.

- [ ] **Step 3: Rewrite the routes module**

Replace the whole of `backend/app/api/routes/users.py` with:

```python
"""User management routes.

Admin-only: list, create, update, reset password, delete. Any signed-in user:
``GET /users/me`` (self-service ``PATCH /users/me`` and ``PUT
/users/me/password`` are added alongside). Lock-out rules live in the service
layer and surface here as **409**; every mutation writes an audit row.

Route order matters: the ``/me`` routes are declared before ``/{user_id}`` so
they can never be captured by the integer path parameter.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.models.enums import AuditAction
from app.models.user import User
from app.schemas.user import PasswordReset, UserCreate, UserRead, UserUpdate
from app.services import user as user_service
from app.services.audit import record_audit

router = APIRouter(tags=["users"])


# --- helpers ------------------------------------------------------------------


async def _get_or_404(db: AsyncSession, user_id: int) -> User:
    user = await user_service.get_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


async def _audit(
    db: AsyncSession,
    *,
    actor: User,
    action: AuditAction,
    object_id: int | None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Record one ``user`` audit row and commit it (same short-transaction
    pattern as ``_config_writes.after_config_write``)."""
    await record_audit(
        db,
        actor=actor.email,
        action=action,
        object_type="user",
        object_id=object_id,
        meta=meta,
    )
    await db.commit()


def _action_for(changes: dict[str, list[object]]) -> AuditAction:
    """A lone ``is_active`` flip is an enable/disable; anything else is an update."""
    if set(changes) == {"is_active"}:
        return AuditAction.enable if changes["is_active"][1] else AuditAction.disable
    return AuditAction.update


def _conflict(exc: user_service.UserProtectionError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


# --- self ---------------------------------------------------------------------


@router.get("/me", response_model=UserRead)
async def read_current_user(current_user: CurrentUser) -> UserRead:
    """Return the authenticated caller."""
    return UserRead.model_validate(current_user)


# --- admin: collection --------------------------------------------------------


@router.get("", response_model=list[UserRead])
async def list_users(
    _admin: AdminUser,
    db: SessionDep,
) -> list[UserRead]:
    """List all users. Admin-only."""
    users = await user_service.list_users(db)
    return [UserRead.model_validate(u) for u in users]


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    admin: AdminUser,
    db: SessionDep,
) -> UserRead:
    """Create a user with an explicit role. Admin-only."""
    try:
        user = await user_service.create_user(
            db,
            email=body.email,
            password=body.password,
            full_name=body.full_name,
            role=body.role,
            is_active=body.is_active,
        )
    except user_service.EmailAlreadyExistsError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with that email already exists",
        ) from None
    await _audit(
        db,
        actor=admin,
        action=AuditAction.create,
        object_id=user.id,
        meta={"email": user.email, "role": user.role.value, "is_active": user.is_active},
    )
    return UserRead.model_validate(user)


# --- admin: single user -------------------------------------------------------


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: int,
    body: UserUpdate,
    admin: AdminUser,
    db: SessionDep,
) -> UserRead:
    """Partially update another user (name, role, active). Admin-only.

    409 when the change would lock the caller out or remove the last admin.
    """
    user = await _get_or_404(db, user_id)
    try:
        user, changes = await user_service.update_user(
            db,
            user,
            actor=admin,
            full_name=body.full_name,
            role=body.role,
            is_active=body.is_active,
        )
    except user_service.UserProtectionError as exc:
        raise _conflict(exc) from None
    if changes:
        action = _action_for(changes)
        meta = {"changes": changes} if action == AuditAction.update else None
        await _audit(db, actor=admin, action=action, object_id=user.id, meta=meta)
    return UserRead.model_validate(user)


@router.put("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    user_id: int,
    body: PasswordReset,
    admin: AdminUser,
    db: SessionDep,
) -> None:
    """Set a new password for another user. Admin-only."""
    user = await _get_or_404(db, user_id)
    await user_service.set_password(db, user, body.password)
    await _audit(
        db, actor=admin, action=AuditAction.update, object_id=user.id, meta={"password_reset": True}
    )


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    admin: AdminUser,
    db: SessionDep,
) -> None:
    """Hard-delete a user. Admin-only. 409 under the lock-out rules."""
    user = await _get_or_404(db, user_id)
    snapshot = {"email": user.email, "role": user.role.value}
    try:
        await user_service.delete_user(db, user, actor=admin)
    except user_service.UserProtectionError as exc:
        raise _conflict(exc) from None
    await _audit(db, actor=admin, action=AuditAction.delete, object_id=user_id, meta=snapshot)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker exec megoopm-test python -m pytest tests/test_users_management.py tests/test_users_rbac.py tests/test_audit_log.py -p no:cacheprovider`
Expected: all pass (`13 passed` in the new file).

If `test_admin_handover_between_two_admins` fails on the final assertion with 200, re-read `assert_no_lockout`: the self rule must fire for `new_active=False` regardless of admin count.

- [ ] **Step 5: Lint, normalize, commit**

```bash
docker exec megoopm-test ruff format app/api/routes/users.py tests/test_users_management.py
docker exec megoopm-test ruff check app/api/routes/users.py tests/test_users_management.py
sed -i 's/\r$//' backend/app/api/routes/users.py backend/tests/test_users_management.py
git add backend/app/api/routes/users.py backend/tests/test_users_management.py
git commit -m "feat(users): admin update, password reset and delete routes with audit

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Self-service routes — PATCH /users/me, PUT /users/me/password

**Files:**
- Modify: `backend/app/api/routes/users.py` (insert after `read_current_user`, before the `# --- admin: collection` section)
- Test: `backend/tests/test_users_management.py` (append)

**Interfaces:**
- Consumes: `ProfileUpdate`, `PasswordChange` (Task 1); `change_own_password`, `InvalidCurrentPasswordError` (Task 2); `_audit` (Task 3).
- Produces: `PATCH /api/v1/users/me` → `200 UserRead`; `PUT /api/v1/users/me/password` → `204` | `400 {"detail": "Current password is incorrect"}`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_users_management.py`:

```python
# --- self-service -------------------------------------------------------------


async def test_member_updates_own_display_name_only(
    db_client: AsyncClient, member_token: str, session_factory: async_sessionmaker
) -> None:
    resp = await db_client.patch(ME, headers=_auth(member_token), json={"full_name": "Renamed"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["full_name"] == "Renamed"
    assert resp.json()["role"] == "member"

    # Role/active are not part of the profile schema — rejected outright.
    resp = await db_client.patch(ME, headers=_auth(member_token), json={"full_name": "x", "role": "admin"})
    assert resp.status_code == 422

    rows = await _audit_rows(session_factory)
    assert rows[-1].actor == "member@example.com"
    assert rows[-1].meta == {"changes": {"full_name": ["Member User", "Renamed"]}}


async def test_profile_update_requires_authentication(db_client: AsyncClient) -> None:
    assert (await db_client.patch(ME, json={"full_name": "x"})).status_code == 401


async def test_member_changes_own_password(
    db_client: AsyncClient, member_token: str, session_factory: async_sessionmaker
) -> None:
    wrong = await db_client.put(
        f"{ME}/password",
        headers=_auth(member_token),
        json={"current_password": "nope", "new_password": "brandnew123"},
    )
    assert wrong.status_code == 400
    assert wrong.json()["detail"] == "Current password is incorrect"

    ok = await db_client.put(
        f"{ME}/password",
        headers=_auth(member_token),
        json={"current_password": "memberpass123", "new_password": "brandnew123"},
    )
    assert ok.status_code == 204, ok.text
    assert await _login(db_client, "member@example.com", "brandnew123")

    rows = await _audit_rows(session_factory)
    assert rows[-1].actor == "member@example.com"
    assert rows[-1].meta == {"password_changed": True}
    assert "brandnew123" not in str(rows[-1].meta)


async def test_password_change_requires_authentication(db_client: AsyncClient) -> None:
    resp = await db_client.put(
        f"{ME}/password", json={"current_password": "a", "new_password": "brandnew123"}
    )
    assert resp.status_code == 401
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker exec megoopm-test python -m pytest tests/test_users_management.py -k "own or profile or password_change" -p no:cacheprovider`
Expected: FAIL — `PATCH /users/me` returns `405`, `PUT /users/me/password` returns `422` (routed to `/{user_id}/password` with `user_id="me"`).

- [ ] **Step 3: Add the self-service routes**

In `backend/app/api/routes/users.py` extend the schema import:

```python
from app.schemas.user import (
    PasswordChange,
    PasswordReset,
    ProfileUpdate,
    UserCreate,
    UserRead,
    UserUpdate,
)
```

Insert immediately after `read_current_user` (still in the `# --- self` section):

```python
@router.patch("/me", response_model=UserRead)
async def update_current_user(
    body: ProfileUpdate,
    current_user: CurrentUser,
    db: SessionDep,
) -> UserRead:
    """Edit the caller's own display name."""
    user, changes = await user_service.update_user(
        db, current_user, actor=current_user, full_name=body.full_name
    )
    if changes:
        await _audit(
            db,
            actor=current_user,
            action=AuditAction.update,
            object_id=user.id,
            meta={"changes": changes},
        )
    return UserRead.model_validate(user)


@router.put("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_current_user_password(
    body: PasswordChange,
    current_user: CurrentUser,
    db: SessionDep,
) -> None:
    """Change the caller's own password after re-verifying the current one."""
    try:
        await user_service.change_own_password(
            db,
            current_user,
            current_password=body.current_password,
            new_password=body.new_password,
        )
    except user_service.InvalidCurrentPasswordError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect"
        ) from None
    await _audit(
        db,
        actor=current_user,
        action=AuditAction.update,
        object_id=current_user.id,
        meta={"password_changed": True},
    )
```

- [ ] **Step 4: Run the full backend suite**

Run: `docker exec megoopm-test python -m pytest -p no:cacheprovider`
Expected: everything passes except `tests/test_openapi.py::test_committed_openapi_is_in_sync` (the contract is stale — Task 5 fixes it). Note the count.

- [ ] **Step 5: Lint, normalize, commit**

```bash
docker exec megoopm-test ruff format app/api/routes/users.py tests/test_users_management.py
docker exec megoopm-test ruff check app/api/routes/users.py tests/test_users_management.py
sed -i 's/\r$//' backend/app/api/routes/users.py backend/tests/test_users_management.py
git add backend/app/api/routes/users.py backend/tests/test_users_management.py
git commit -m "feat(users): self-service profile edit and password change

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Regenerate the API contract

**Files:**
- Regenerate: `backend/openapi.json`, `frontend/src/lib/api/generated/schema.ts`

**Interfaces:**
- Produces: `Schemas["UserUpdate"]`, `Schemas["PasswordReset"]`, `Schemas["PasswordChange"]`, `Schemas["ProfileUpdate"]` in the generated TS, which Task 7 relies on.

- [ ] **Step 1: Confirm the drift test currently fails**

Run: `docker exec megoopm-test python -m pytest tests/test_openapi.py -p no:cacheprovider`
Expected: `test_committed_openapi_is_in_sync` FAILS with "backend/openapi.json is stale".

- [ ] **Step 2: Export the backend contract**

Run: `docker exec megoopm-test python -m scripts.export_openapi`
Then: `git diff --stat backend/openapi.json` — expect a change; `grep -c '"/api/v1/users/me/password"' backend/openapi.json` → `1`.

- [ ] **Step 3: Generate the frontend types**

```bash
cd frontend && npm ci && npm run gen:api && cd ..
grep -n 'PasswordChange\|ProfileUpdate\|UserUpdate' frontend/src/lib/api/generated/schema.ts | head
```
Expected: the three schema names appear.

- [ ] **Step 4: Verify both gates**

Run: `docker exec megoopm-test python -m pytest tests/test_openapi.py -p no:cacheprovider` → all pass.
Run (from `frontend/`): `npm run typecheck` → passes (nothing consumes the new types yet).

- [ ] **Step 5: Normalize, commit**

```bash
sed -i 's/\r$//' backend/openapi.json frontend/src/lib/api/generated/schema.ts
git add backend/openapi.json frontend/src/lib/api/generated/schema.ts
git commit -m "chore(api): regenerate OpenAPI contract for user management routes

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Navigation — `adminOnly`, `navForRole`, `utilityRoutes`, sidebar + topbar title

**Files:**
- Modify: `frontend/src/config/nav.ts`, `frontend/src/config/nav.test.ts`, `frontend/src/components/app-sidebar.tsx:1-8,58`, `frontend/src/components/app-topbar.tsx:21-26`

**Interfaces:**
- Consumes: `useAuth()` from `@/lib/auth/context` (`user.role`).
- Produces: `NavItem.adminOnly?: boolean`; `navForRole(role: "admin" | "member" | null | undefined): NavItem[]`; `utilityRoutes: Record<string, string>` (`{ "/account": "Account" }`). Task 11 adds the `/account` page that `utilityRoutes` names.

- [ ] **Step 1: Write the failing tests**

Replace `frontend/src/config/nav.test.ts` with:

```ts
import { describe, expect, it } from "vitest";

import { HOME_ROUTE, navForRole, primaryNav, utilityRoutes } from "@/config/nav";

describe("primaryNav", () => {
  it("covers every MegooPM product area", () => {
    const titles = primaryNav.map((item) => item.title);
    expect(titles).toEqual([
      "Proxy Hosts",
      "Certificates",
      "Access Lists",
      "Streams",
      "Redirection Hosts",
      "404 Hosts",
      "Security",
      "Users",
    ]);
  });

  it("uses absolute, unique hrefs", () => {
    const hrefs = primaryNav.map((item) => item.href);
    expect(hrefs.every((href) => href.startsWith("/"))).toBe(true);
    expect(new Set(hrefs).size).toBe(hrefs.length);
  });

  it("points the home route at a real nav destination", () => {
    expect(primaryNav.some((item) => item.href === HOME_ROUTE)).toBe(true);
  });

  it("marks only Users as admin-only", () => {
    const adminOnly = primaryNav.filter((item) => item.adminOnly).map((item) => item.href);
    expect(adminOnly).toEqual(["/users"]);
  });
});

describe("navForRole", () => {
  it("shows admin-only items to admins", () => {
    expect(navForRole("admin").map((i) => i.href)).toContain("/users");
  });

  it("hides admin-only items from members and signed-out visitors", () => {
    expect(navForRole("member").map((i) => i.href)).not.toContain("/users");
    expect(navForRole(null).map((i) => i.href)).not.toContain("/users");
    expect(navForRole(undefined).map((i) => i.href)).not.toContain("/users");
  });

  it("keeps the public items and their order for every role", () => {
    const publicHrefs = primaryNav.filter((i) => !i.adminOnly).map((i) => i.href);
    expect(navForRole("member").map((i) => i.href)).toEqual(publicHrefs);
  });
});

describe("utilityRoutes", () => {
  it("names the account page, which is not in the sidebar", () => {
    expect(utilityRoutes["/account"]).toBe("Account");
    expect(primaryNav.some((i) => i.href === "/account")).toBe(false);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npx vitest run src/config/nav.test.ts`
Expected: FAIL — `navForRole` / `utilityRoutes` are not exported; the titles list lacks "Users".

- [ ] **Step 3: Implement in `nav.ts`**

In `frontend/src/config/nav.ts`: add `Users` to the lucide import list (`Users,` after `ShieldAlert,`), add the field to `NavItem`, append the item, and add the helpers:

```ts
export interface NavItem {
  title: string;
  href: string;
  icon: LucideIcon;
  /** Short description used for the page header / tooltips. */
  description: string;
  /** Only rendered for `admin` users (see `navForRole`). */
  adminOnly?: boolean;
}
```

Append to `primaryNav` after the Security entry:

```ts
  {
    title: "Users",
    href: "/users",
    icon: Users,
    description: "Accounts and roles for people who sign in to MegooPM.",
    adminOnly: true,
  },
```

Add after `HOME_ROUTE`:

```ts
/**
 * The sidebar items a user with `role` may see. Admin-only entries are
 * hidden from members and from signed-out visitors (`null`/`undefined`);
 * the API's RBAC (403) remains the enforcement.
 */
export function navForRole(role: "admin" | "member" | null | undefined): NavItem[] {
  return primaryNav.filter((item) => !item.adminOnly || role === "admin");
}

/**
 * Pages reachable from the account menu rather than the sidebar. The topbar
 * uses this to title them; they are deliberately absent from `primaryNav`.
 */
export const utilityRoutes: Record<string, string> = {
  "/account": "Account",
};
```

- [ ] **Step 4: Wire the sidebar and topbar**

`frontend/src/components/app-sidebar.tsx` — change the nav import and add the auth hook:

```ts
import { navForRole } from "@/config/nav";
import { APP_NAME } from "@/lib/env";
import { useAuth } from "@/lib/auth/context";
```

Inside `AppSidebar`, after `const pathname = usePathname();`:

```ts
  const { user } = useAuth();
  const items = navForRole(user?.role);
```

and change `{primaryNav.map((item) => (` to `{items.map((item) => (`.

`frontend/src/components/app-topbar.tsx` — change the import to `import { primaryNav, utilityRoutes } from "@/config/nav";` and replace `currentTitle` with:

```ts
function currentTitle(pathname: string): string {
  const match = primaryNav.find(
    (item) => pathname === item.href || pathname.startsWith(`${item.href}/`),
  );
  return match?.title ?? utilityRoutes[pathname] ?? "Dashboard";
}
```

- [ ] **Step 5: Run tests, lint, typecheck**

Run (from `frontend/`): `npx vitest run src/config/nav.test.ts && npm run lint && npm run typecheck`
Expected: `8 passed`; lint and typecheck clean.

- [ ] **Step 6: Normalize, commit**

```bash
sed -i 's/\r$//' frontend/src/config/nav.ts frontend/src/config/nav.test.ts frontend/src/components/app-sidebar.tsx frontend/src/components/app-topbar.tsx
git add frontend/src/config/nav.ts frontend/src/config/nav.test.ts frontend/src/components/app-sidebar.tsx frontend/src/components/app-topbar.tsx
git commit -m "feat(nav): admin-only Users entry, role-filtered sidebar, utility route titles

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Typed `users` API resource

**Files:**
- Create: `frontend/src/lib/api/resources/users.ts`, `frontend/src/lib/api/resources/users.test.ts`
- Modify: `frontend/src/lib/api/index.ts` (append exports)

**Interfaces:**
- Consumes: `api` from `@/lib/api/client`; `Schemas` from `@/lib/api/types` (Task 5 output).
- Produces:
  ```ts
  type User = Schemas["UserRead"]; type UserCreate = Schemas["UserCreate"];
  type UserUpdate = Schemas["UserUpdate"]; type PasswordReset = Schemas["PasswordReset"];
  type PasswordChange = Schemas["PasswordChange"]; type ProfileUpdate = Schemas["ProfileUpdate"];
  type UserRole = Schemas["UserRole"];
  users.list(): Promise<User[]>; users.create(body: UserCreate): Promise<User>;
  users.update(id: number, body: UserUpdate): Promise<User>;
  users.resetPassword(id: number, body: PasswordReset): Promise<void>;
  users.remove(id: number): Promise<void>;
  users.updateMe(body: ProfileUpdate): Promise<User>;
  users.changeMyPassword(body: PasswordChange): Promise<void>;
  USER_ROLES: readonly UserRole[]; USER_ROLE_LABELS: Record<UserRole, string>;
  ```

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/api/resources/users.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api/client";
import { USER_ROLE_LABELS, USER_ROLES, users } from "@/lib/api/resources/users";

describe("users resource", () => {
  afterEach(() => vi.restoreAllMocks());

  it("targets the users collection and members", async () => {
    vi.spyOn(api, "get").mockResolvedValue([] as never);
    vi.spyOn(api, "post").mockResolvedValue({} as never);
    vi.spyOn(api, "patch").mockResolvedValue({} as never);
    vi.spyOn(api, "put").mockResolvedValue(undefined as never);
    vi.spyOn(api, "delete").mockResolvedValue(undefined as never);

    await users.list();
    await users.create({ email: "a@b.c", password: "password123", full_name: "", role: "member", is_active: true });
    await users.update(7, { role: "admin" });
    await users.resetPassword(7, { password: "brandnew123" });
    await users.remove(7);
    await users.updateMe({ full_name: "Me" });
    await users.changeMyPassword({ current_password: "old", new_password: "brandnew123" });

    expect(api.get).toHaveBeenCalledWith("/api/v1/users");
    expect(api.post).toHaveBeenCalledWith("/api/v1/users", expect.objectContaining({ email: "a@b.c" }));
    expect(api.patch).toHaveBeenCalledWith("/api/v1/users/7", { role: "admin" });
    expect(api.put).toHaveBeenCalledWith("/api/v1/users/7/password", { password: "brandnew123" });
    expect(api.delete).toHaveBeenCalledWith("/api/v1/users/7");
    expect(api.patch).toHaveBeenCalledWith("/api/v1/users/me", { full_name: "Me" });
    expect(api.put).toHaveBeenCalledWith("/api/v1/users/me/password", {
      current_password: "old",
      new_password: "brandnew123",
    });
  });

  it("labels every role", () => {
    for (const role of USER_ROLES) expect(USER_ROLE_LABELS[role]).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npx vitest run src/lib/api/resources/users.test.ts`
Expected: FAIL — cannot resolve `@/lib/api/resources/users`.

- [ ] **Step 3: Create the resource**

Create `frontend/src/lib/api/resources/users.ts`:

```ts
/**
 * Typed client for user management and self-service account endpoints.
 *
 * Admin-only: list/create/update/resetPassword/remove. Any signed-in user:
 * updateMe/changeMyPassword. Shapes come from the generated OpenAPI schema;
 * the API's lock-out rules surface as 409s with a human-readable `detail`.
 */
import { api } from "@/lib/api/client";
import type { Schemas } from "@/lib/api/types";

export type User = Schemas["UserRead"];
export type UserCreate = Schemas["UserCreate"];
export type UserUpdate = Schemas["UserUpdate"];
export type PasswordReset = Schemas["PasswordReset"];
export type PasswordChange = Schemas["PasswordChange"];
export type ProfileUpdate = Schemas["ProfileUpdate"];
export type UserRole = Schemas["UserRole"];

const BASE = "/api/v1/users";

export const users = {
  list: () => api.get<User[]>(BASE),
  create: (body: UserCreate) => api.post<User>(BASE, body),
  update: (id: number, body: UserUpdate) => api.patch<User>(`${BASE}/${id}`, body),
  resetPassword: (id: number, body: PasswordReset) =>
    api.put<void>(`${BASE}/${id}/password`, body),
  remove: (id: number) => api.delete<void>(`${BASE}/${id}`),
  /** The caller's own profile (display name only). */
  updateMe: (body: ProfileUpdate) => api.patch<User>(`${BASE}/me`, body),
  /** The caller's own password; 400 when the current password is wrong. */
  changeMyPassword: (body: PasswordChange) => api.put<void>(`${BASE}/me/password`, body),
} as const;

export const USER_ROLES: readonly UserRole[] = ["admin", "member"] as const;

export const USER_ROLE_LABELS: Record<UserRole, string> = {
  admin: "Admin",
  member: "Member",
};
```

Append to `frontend/src/lib/api/index.ts`:

```ts
export { users, USER_ROLES, USER_ROLE_LABELS } from "@/lib/api/resources/users";
export type {
  User,
  UserCreate,
  UserUpdate,
  PasswordReset,
  PasswordChange,
  ProfileUpdate,
  UserRole,
} from "@/lib/api/resources/users";
```

- [ ] **Step 4: Run tests, lint, typecheck**

Run (from `frontend/`): `npx vitest run src/lib/api/resources/users.test.ts && npm run lint && npm run typecheck`
Expected: `2 passed`; clean.

- [ ] **Step 5: Normalize, commit**

```bash
sed -i 's/\r$//' frontend/src/lib/api/resources/users.ts frontend/src/lib/api/resources/users.test.ts frontend/src/lib/api/index.ts
git add frontend/src/lib/api/resources/users.ts frontend/src/lib/api/resources/users.test.ts frontend/src/lib/api/index.ts
git commit -m "feat(api): typed users resource

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Pure helpers for the Users/Account UI

**Files:**
- Create: `frontend/src/components/users/lib.ts`, `frontend/src/components/users/lib.test.ts`

**Interfaces:**
- Consumes: `User` from `@/lib/api`.
- Produces: `displayName(user: Pick<User, "full_name" | "email">): string`; `isSelf(user: Pick<User, "id">, current: Pick<User, "id"> | null | undefined): boolean`; `validateNewPassword(password: string, confirm: string): string | null` (error message or `null`); `MIN_PASSWORD_LENGTH = 8`. Tasks 9–11 use these.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/users/lib.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { displayName, isSelf, MIN_PASSWORD_LENGTH, validateNewPassword } from "@/components/users/lib";

describe("displayName", () => {
  it("prefers the full name and falls back to the email", () => {
    expect(displayName({ full_name: "Ada Lovelace", email: "ada@example.com" })).toBe("Ada Lovelace");
    expect(displayName({ full_name: "", email: "ada@example.com" })).toBe("ada@example.com");
    expect(displayName({ full_name: "   ", email: "ada@example.com" })).toBe("ada@example.com");
  });
});

describe("isSelf", () => {
  it("matches on id only", () => {
    expect(isSelf({ id: 1 }, { id: 1 })).toBe(true);
    expect(isSelf({ id: 1 }, { id: 2 })).toBe(false);
    expect(isSelf({ id: 1 }, null)).toBe(false);
    expect(isSelf({ id: 1 }, undefined)).toBe(false);
  });
});

describe("validateNewPassword", () => {
  it("enforces the minimum length", () => {
    expect(validateNewPassword("short", "short")).toBe(
      `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`,
    );
  });

  it("requires the confirmation to match", () => {
    expect(validateNewPassword("longenough", "different")).toBe("Passwords do not match.");
  });

  it("returns null for a valid pair", () => {
    expect(validateNewPassword("longenough", "longenough")).toBeNull();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/users/lib.test.ts`
Expected: FAIL — cannot resolve `@/components/users/lib`.

- [ ] **Step 3: Implement**

Create `frontend/src/components/users/lib.ts`:

```ts
/**
 * Pure helpers for the Users and Account UI. React-free so the rules that
 * drive disabled controls and password validation are unit-testable.
 */
import type { User } from "@/lib/api";

/** Mirrors the backend's `min_length=8` on password fields. */
export const MIN_PASSWORD_LENGTH = 8;

export function displayName(user: Pick<User, "full_name" | "email">): string {
  const name = user.full_name.trim();
  return name.length > 0 ? name : user.email;
}

/** Whether `user` is the signed-in account (controls the "You" badge / disabled actions). */
export function isSelf(
  user: Pick<User, "id">,
  current: Pick<User, "id"> | null | undefined,
): boolean {
  return current != null && current.id === user.id;
}

/** Client-side pre-check for password forms; returns a message or `null` when valid. */
export function validateNewPassword(password: string, confirm: string): string | null {
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`;
  }
  if (password !== confirm) return "Passwords do not match.";
  return null;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run (from `frontend/`): `npx vitest run src/components/users/lib.test.ts`
Expected: `5 passed`.

- [ ] **Step 5: Normalize, commit**

```bash
sed -i 's/\r$//' frontend/src/components/users/lib.ts frontend/src/components/users/lib.test.ts
git add frontend/src/components/users/lib.ts frontend/src/components/users/lib.test.ts
git commit -m "feat(users-ui): pure helpers for display name, self detection, password validation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: User dialog (create/edit) and reset-password dialog

**Files:**
- Create: `frontend/src/components/users/user-dialog.tsx`, `frontend/src/components/users/reset-password-dialog.tsx`

**Interfaces:**
- Consumes: `users`, `USER_ROLES`, `USER_ROLE_LABELS`, types from `@/lib/api` (Task 7); `validateNewPassword`, `isSelf` (Task 8); `describeError` from `@/components/proxy-hosts/lib`; shadcn `Dialog*`, `Input`, `Label`, `Select*`, `Switch`, `Button`.
- Produces:
  ```tsx
  <UserDialog open onOpenChange user={User | null} currentUser={User | null} onSaved={() => void} />
  <ResetPasswordDialog open onOpenChange user={User | null} onSaved={() => void} />
  ```
  `user === null` means create mode. `currentUser` lets edit mode disable role/active for your own row.

These are UI composition (no new logic beyond Task 8's helpers); they are exercised by Task 10's view test through mocks and by the live check in Task 13.

- [ ] **Step 1: Create `user-dialog.tsx`**

```tsx
"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
  USER_ROLES,
  USER_ROLE_LABELS,
  users,
  type User,
  type UserRole,
} from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { isSelf, MIN_PASSWORD_LENGTH } from "@/components/users/lib";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

export function UserDialog({
  open,
  onOpenChange,
  user,
  currentUser,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` = create mode; otherwise the user being edited. */
  user: User | null;
  /** The signed-in user; your own role/active controls are disabled. */
  currentUser: User | null;
  onSaved: () => void;
}) {
  const isEdit = user !== null;
  const editingSelf = user !== null && isSelf(user, currentUser);

  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [role, setRole] = useState<UserRole>("member");
  const [isActive, setIsActive] = useState(true);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Reset the form whenever the dialog (re)opens for a different user.
  useEffect(() => {
    if (!open) return;
    setEmail(user?.email ?? "");
    setFullName(user?.full_name ?? "");
    setRole(user?.role ?? "member");
    setIsActive(user?.is_active ?? true);
    setPassword("");
    setError(null);
  }, [open, user]);

  async function submit() {
    setError(null);
    if (!isEdit) {
      if (!email.trim()) return setError("Enter an email address.");
      if (password.length < MIN_PASSWORD_LENGTH) {
        return setError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      }
    }

    setSaving(true);
    try {
      if (isEdit) {
        await users.update(user.id, {
          full_name: fullName.trim(),
          ...(editingSelf ? {} : { role, is_active: isActive }),
        });
        toast.success("User updated");
      } else {
        await users.create({
          email: email.trim(),
          password,
          full_name: fullName.trim(),
          role,
          is_active: isActive,
        });
        toast.success("User created");
      }
      onOpenChange(false);
      onSaved();
    } catch (err) {
      const described = describeError(err);
      setError(described.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit user" : "New user"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Change the display name, role, or whether the account can sign in."
              : "Create an account. Share the password with the person out of band."}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="user-email">Email</Label>
            <Input
              id="user-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="person@example.com"
              disabled={saving || isEdit}
              readOnly={isEdit}
            />
            {isEdit ? (
              <p className="text-xs text-muted-foreground">Email is the account identity and cannot be changed.</p>
            ) : null}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="user-name">Full name</Label>
            <Input
              id="user-name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Optional"
              disabled={saving}
            />
          </div>
          {!isEdit ? (
            <div className="space-y-1.5">
              <Label htmlFor="user-password">Password</Label>
              <Input
                id="user-password"
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={saving}
              />
            </div>
          ) : null}
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="user-role">Role</Label>
              <Select value={role} onValueChange={(v) => setRole(v as UserRole)}>
                <SelectTrigger id="user-role" disabled={saving || editingSelf}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {USER_ROLES.map((r) => (
                    <SelectItem key={r} value={r}>
                      {USER_ROLE_LABELS[r]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <label className="flex items-center gap-2 self-end pb-2">
              <Switch
                checked={isActive}
                onCheckedChange={setIsActive}
                disabled={saving || editingSelf}
              />
              <span className="text-sm font-medium">Active</span>
            </label>
          </div>
          {editingSelf ? (
            <p className="text-xs text-muted-foreground">
              You cannot change your own role or deactivate your own account.
            </p>
          ) : null}

          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={saving}>
            {saving ? "Saving…" : isEdit ? "Save changes" : "Create user"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 2: Create `reset-password-dialog.tsx`**

```tsx
"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { users, type User } from "@/lib/api";
import { describeError } from "@/components/proxy-hosts/lib";
import { displayName, validateNewPassword } from "@/components/users/lib";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function ResetPasswordDialog({
  open,
  onOpenChange,
  user,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  user: User | null;
  onSaved: () => void;
}) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setPassword("");
    setConfirm("");
    setError(null);
  }, [open, user]);

  async function submit() {
    if (!user) return;
    const invalid = validateNewPassword(password, confirm);
    if (invalid) return setError(invalid);
    setError(null);
    setSaving(true);
    try {
      await users.resetPassword(user.id, { password });
      toast.success(`Password reset for ${displayName(user)}`);
      onOpenChange(false);
      onSaved();
    } catch (err) {
      setError(describeError(err).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Reset password</DialogTitle>
          <DialogDescription>
            {user ? `Set a new password for ${displayName(user)}. Share it out of band.` : ""}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="reset-password">New password</Label>
            <Input
              id="reset-password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={saving}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="reset-confirm">Confirm password</Label>
            <Input
              id="reset-confirm"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              disabled={saving}
            />
          </div>
          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={saving}>
            {saving ? "Saving…" : "Set password"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 3: Lint and typecheck**

Run (from `frontend/`): `npm run lint && npm run typecheck`
Expected: clean. (`Switch` is base-ui: `onCheckedChange={setIsActive}` is exactly how `upstream-dialog.tsx` uses it.)

- [ ] **Step 4: Normalize, commit**

```bash
sed -i 's/\r$//' frontend/src/components/users/user-dialog.tsx frontend/src/components/users/reset-password-dialog.tsx
git add frontend/src/components/users/user-dialog.tsx frontend/src/components/users/reset-password-dialog.tsx
git commit -m "feat(users-ui): create/edit and reset-password dialogs

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Users page — table view, delete, route

**Files:**
- Create: `frontend/src/components/users/users-view.tsx`, `frontend/src/components/users/users-view.test.tsx`, `frontend/src/app/(app)/users/page.tsx`

**Interfaces:**
- Consumes: `users`, `USER_ROLE_LABELS`, `User` (Task 7); `displayName`, `isSelf` (Task 8); `UserDialog`, `ResetPasswordDialog` (Task 9); `ConfirmDeleteDialog` and `describeError` from `@/components/proxy-hosts/`; `useAuth()`.
- Produces: `<UsersView />`; route `/users`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/users/users-view.test.tsx`:

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { users } from "@/lib/api";
import { UsersView } from "@/components/users/users-view";

const admin = {
  id: 1,
  email: "admin@example.com",
  full_name: "Admin User",
  role: "admin" as const,
  is_active: true,
  created_at: "2026-08-27T09:00:00Z",
  updated_at: "2026-08-27T09:00:00Z",
};
const member = { ...admin, id: 2, email: "member@example.com", full_name: "", role: "member" as const, is_active: false };

vi.mock("@/lib/auth/context", () => ({
  useAuth: () => ({ user: admin, status: "authenticated", login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }),
}));

// Dialogs own Select/portal machinery irrelevant here; we only check wiring.
vi.mock("@/components/users/user-dialog", () => ({
  UserDialog: ({ open, onSaved }: { open: boolean; onSaved: () => void }) =>
    open ? (
      <button type="button" onClick={onSaved}>
        confirm-save
      </button>
    ) : null,
}));
vi.mock("@/components/users/reset-password-dialog", () => ({
  ResetPasswordDialog: ({ open }: { open: boolean }) => (open ? <div>reset-dialog</div> : null),
}));
vi.mock("@/components/proxy-hosts/confirm-delete-dialog", () => ({
  ConfirmDeleteDialog: ({
    open,
    onConfirm,
    onDeleted,
  }: {
    open: boolean;
    onConfirm: () => Promise<void>;
    onDeleted: () => void;
  }) =>
    open ? (
      <button
        type="button"
        onClick={() => {
          void onConfirm().then(onDeleted);
        }}
      >
        confirm-delete
      </button>
    ) : null,
}));

describe("UsersView", () => {
  beforeEach(() => {
    vi.spyOn(users, "list").mockResolvedValue([admin, member]);
    vi.spyOn(users, "remove").mockResolvedValue(undefined);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("lists users with role and status", async () => {
    render(<UsersView />);
    expect(await screen.findByText("member@example.com")).toBeInTheDocument();
    expect(screen.getByText("Admin User")).toBeInTheDocument();
    expect(screen.getAllByText("Admin").length).toBeGreaterThan(0);
    expect(screen.getByText("Inactive")).toBeInTheDocument();
  });

  it("marks the signed-in user's row and disables its delete action", async () => {
    render(<UsersView />);
    const row = (await screen.findByText("admin@example.com")).closest("tr") as HTMLElement;
    expect(within(row).getByText("You")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Delete admin@example.com" })).toBeDisabled();
    const other = screen.getByText("member@example.com").closest("tr") as HTMLElement;
    expect(within(other).getByRole("button", { name: "Delete member@example.com" })).toBeEnabled();
  });

  it("deletes a user and refetches", async () => {
    const user = userEvent.setup();
    render(<UsersView />);
    const other = (await screen.findByText("member@example.com")).closest("tr") as HTMLElement;
    await user.click(within(other).getByRole("button", { name: "Delete member@example.com" }));
    await user.click(screen.getByRole("button", { name: "confirm-delete" }));
    await waitFor(() => expect(users.remove).toHaveBeenCalledWith(2));
    await waitFor(() => expect(users.list).toHaveBeenCalledTimes(2));
  });

  it("refetches after the create dialog saves", async () => {
    const user = userEvent.setup();
    render(<UsersView />);
    await screen.findByText("member@example.com");
    await user.click(screen.getByRole("button", { name: /new user/i }));
    await user.click(screen.getByRole("button", { name: "confirm-save" }));
    await waitFor(() => expect(users.list).toHaveBeenCalledTimes(2));
  });

  it("shows the load error state", async () => {
    vi.spyOn(users, "list").mockRejectedValueOnce(new Error("boom"));
    render(<UsersView />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/users/users-view.test.tsx`
Expected: FAIL — cannot resolve `@/components/users/users-view`.

- [ ] **Step 3: Create the view**

Create `frontend/src/components/users/users-view.tsx`:

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { KeyRound, Pencil, Plus, Trash2, Users as UsersIcon } from "lucide-react";

import { USER_ROLE_LABELS, users, type User } from "@/lib/api";
import { useAuth } from "@/lib/auth/context";
import { describeError } from "@/components/proxy-hosts/lib";
import { ConfirmDeleteDialog } from "@/components/proxy-hosts/confirm-delete-dialog";
import { displayName, isSelf } from "@/components/users/lib";
import { ResetPasswordDialog } from "@/components/users/reset-password-dialog";
import { UserDialog } from "@/components/users/user-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

function StatusBadge({ active }: { active: boolean }) {
  return (
    <Badge variant={active ? "success" : "muted"}>
      <span
        className={`size-1.5 rounded-full ${active ? "bg-success" : "bg-muted-foreground"}`}
        aria-hidden
      />
      {active ? "Active" : "Inactive"}
    </Badge>
  );
}

function LoadingRows({ cols }: { cols: number }) {
  return (
    <>
      {[0, 1, 2].map((i) => (
        <TableRow key={i}>
          {Array.from({ length: cols }).map((_, c) => (
            <TableCell key={c}>
              <Skeleton className="h-4 w-full" />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  );
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { dateStyle: "medium" });
}

export function UsersView() {
  const { user: currentUser } = useAuth();
  const [rows, setRows] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [userDialog, setUserDialog] = useState<{ open: boolean; user: User | null }>({
    open: false,
    user: null,
  });
  const [resetTarget, setResetTarget] = useState<User | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<User | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await users.list());
      setLoadError(null);
    } catch (err) {
      setLoadError(describeError(err).message);
    } finally {
      setLoading(false);
    }
  }, []);

  const refresh = useCallback(() => {
    setLoading(true);
    void load();
  }, [load]);

  useEffect(() => {
    let active = true;
    void (async () => {
      if (active) await load();
    })();
    return () => {
      active = false;
    };
  }, [load]);

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <div className="flex items-center gap-3">
        <div className="flex size-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          <UsersIcon className="size-5" />
        </div>
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Users</h2>
          <p className="text-sm text-muted-foreground">
            Accounts and roles for people who sign in to MegooPM.
          </p>
        </div>
      </div>

      {loadError ? (
        <div className="flex flex-col items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
          <p className="text-sm text-destructive" role="alert">
            Couldn’t load users: {loadError}
          </p>
          <Button variant="outline" size="sm" onClick={refresh}>
            Retry
          </Button>
        </div>
      ) : null}

      <div className="space-y-3">
        <div className="flex justify-end">
          <Button size="sm" onClick={() => setUserDialog({ open: true, user: null })}>
            <Plus /> New user
          </Button>
        </div>
        <div className="rounded-xl border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Email</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Created</TableHead>
                <TableHead className="w-32 text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <LoadingRows cols={6} />
              ) : rows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-10 text-center text-muted-foreground">
                    No users yet.
                  </TableCell>
                </TableRow>
              ) : (
                rows.map((u) => {
                  const self = isSelf(u, currentUser);
                  return (
                    <TableRow key={u.id}>
                      <TableCell className="font-medium">
                        <span className="inline-flex items-center gap-2">
                          {displayName(u)}
                          {self ? <Badge variant="outline">You</Badge> : null}
                        </span>
                      </TableCell>
                      <TableCell className="font-mono text-xs">{u.email}</TableCell>
                      <TableCell>
                        <Badge variant={u.role === "admin" ? "default" : "muted"}>
                          {USER_ROLE_LABELS[u.role]}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <StatusBadge active={u.is_active} />
                      </TableCell>
                      <TableCell className="text-muted-foreground">{formatDate(u.created_at)}</TableCell>
                      <TableCell>
                        <div className="flex justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-label={`Edit ${u.email}`}
                            onClick={() => setUserDialog({ open: true, user: u })}
                          >
                            <Pencil />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-label={`Reset password for ${u.email}`}
                            onClick={() => setResetTarget(u)}
                          >
                            <KeyRound />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-label={`Delete ${u.email}`}
                            disabled={self}
                            title={self ? "You cannot delete your own account." : undefined}
                            onClick={() => setDeleteTarget(u)}
                          >
                            <Trash2 />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })
              )}
            </TableBody>
          </Table>
        </div>
      </div>

      <UserDialog
        open={userDialog.open}
        onOpenChange={(open) => setUserDialog((s) => ({ ...s, open }))}
        user={userDialog.user}
        currentUser={currentUser}
        onSaved={refresh}
      />
      <ResetPasswordDialog
        open={resetTarget !== null}
        onOpenChange={(open) => {
          if (!open) setResetTarget(null);
        }}
        user={resetTarget}
        onSaved={refresh}
      />
      <ConfirmDeleteDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        title="Delete user"
        description={
          deleteTarget
            ? `Delete ${displayName(deleteTarget)} (${deleteTarget.email})? They will be signed out immediately. This cannot be undone.`
            : ""
        }
        onConfirm={async () => {
          if (deleteTarget) await users.remove(deleteTarget.id);
        }}
        onDeleted={refresh}
      />
    </div>
  );
}
```

Create `frontend/src/app/(app)/users/page.tsx`:

```tsx
import type { Metadata } from "next";

import { UsersView } from "@/components/users/users-view";

export const metadata: Metadata = { title: "Users" };

export default function UsersPage() {
  return <UsersView />;
}
```

- [ ] **Step 4: Run tests, lint, typecheck**

Run (from `frontend/`): `npx vitest run src/components/users && npm run lint && npm run typecheck`
Expected: `10 passed` across the two files; clean. (`components/ui/badge.tsx` defines `default`, `secondary`, `destructive`, `success`, `warning`, `muted`, `outline` — every variant used above exists.)

- [ ] **Step 5: Normalize, commit**

```bash
sed -i 's/\r$//' frontend/src/components/users/users-view.tsx frontend/src/components/users/users-view.test.tsx 'frontend/src/app/(app)/users/page.tsx'
git add frontend/src/components/users/users-view.tsx frontend/src/components/users/users-view.test.tsx 'frontend/src/app/(app)/users/page.tsx'
git commit -m "feat(users-ui): Users page with table, create/edit, reset password, delete

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: Account page — `refreshUser`, profile + password cards, topbar link

**Files:**
- Modify: `frontend/src/lib/auth/context.tsx` (`AuthContextValue` at lines 49–56; provider body), `frontend/src/components/app-topbar.tsx` (imports; account menu)
- Create: `frontend/src/components/account/account-view.tsx`, `frontend/src/components/account/account-view.test.tsx`, `frontend/src/app/(app)/account/page.tsx`

**Interfaces:**
- Consumes: `users.updateMe`, `users.changeMyPassword` (Task 7); `validateNewPassword` (Task 8); `describeError`; shadcn `Card*`.
- Produces: `AuthContextValue.refreshUser: () => Promise<void>`; `<AccountView />`; route `/account`; topbar "Account" menu item.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/account/account-view.test.tsx`:

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { users } from "@/lib/api";
import { ApiError } from "@/lib/api/errors";
import { AccountView } from "@/components/account/account-view";

const refreshUser = vi.fn().mockResolvedValue(undefined);
const me = {
  id: 2,
  email: "member@example.com",
  full_name: "Member User",
  role: "member" as const,
  is_active: true,
  created_at: "2026-08-27T09:00:00Z",
  updated_at: "2026-08-27T09:00:00Z",
};

vi.mock("@/lib/auth/context", () => ({
  useAuth: () => ({ user: me, status: "authenticated", login: vi.fn(), logout: vi.fn(), refreshUser }),
}));

describe("AccountView", () => {
  beforeEach(() => {
    vi.spyOn(users, "updateMe").mockResolvedValue({ ...me, full_name: "Renamed" });
    vi.spyOn(users, "changeMyPassword").mockResolvedValue(undefined);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    refreshUser.mockClear();
  });

  it("saves the display name and refreshes the session user", async () => {
    const user = userEvent.setup();
    render(<AccountView />);
    const name = screen.getByLabelText("Full name");
    await user.clear(name);
    await user.type(name, "Renamed");
    await user.click(screen.getByRole("button", { name: "Save profile" }));
    await waitFor(() => expect(users.updateMe).toHaveBeenCalledWith({ full_name: "Renamed" }));
    await waitFor(() => expect(refreshUser).toHaveBeenCalled());
  });

  it("blocks a mismatched confirmation without calling the API", async () => {
    const user = userEvent.setup();
    render(<AccountView />);
    await user.type(screen.getByLabelText("Current password"), "memberpass123");
    await user.type(screen.getByLabelText("New password"), "brandnew123");
    await user.type(screen.getByLabelText("Confirm new password"), "different1");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Passwords do not match.");
    expect(users.changeMyPassword).not.toHaveBeenCalled();
  });

  it("surfaces a wrong current password from the API", async () => {
    // ApiError(status, message, body) — see src/lib/api/errors.ts.
    vi.spyOn(users, "changeMyPassword").mockRejectedValueOnce(
      new ApiError(400, "Current password is incorrect", { detail: "Current password is incorrect" }),
    );
    const user = userEvent.setup();
    render(<AccountView />);
    await user.type(screen.getByLabelText("Current password"), "nope");
    await user.type(screen.getByLabelText("New password"), "brandnew123");
    await user.type(screen.getByLabelText("Confirm new password"), "brandnew123");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Current password is incorrect");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/account/account-view.test.tsx`
Expected: FAIL — cannot resolve `@/components/account/account-view`.

- [ ] **Step 3: Add `refreshUser` to the auth context**

In `frontend/src/lib/auth/context.tsx`, extend the interface:

```ts
export interface AuthContextValue {
  user: CurrentUser | null;
  status: AuthStatus;
  /** Authenticate with credentials; throws `ApiError` on failure. */
  login: (email: string, password: string) => Promise<void>;
  /** Clear the session and return to the login page. */
  logout: () => void;
  /** Re-fetch `/users/me` (e.g. after a profile edit) so the shell reflects it. */
  refreshUser: () => Promise<void>;
}
```

In `AuthProvider`, after the `logout` callback add:

```ts
  const refreshUser = useCallback(async () => {
    const me = await fetchCurrentUser();
    setUser(me);
  }, []);
```

and change the memo to:

```ts
  const value = useMemo<AuthContextValue>(
    () => ({ user, status, login, logout, refreshUser }),
    [user, status, login, logout, refreshUser],
  );
```

- [ ] **Step 4: Create the account view and page**

Create `frontend/src/components/account/account-view.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { CircleUser } from "lucide-react";
import { toast } from "sonner";

import { users } from "@/lib/api";
import { useAuth } from "@/lib/auth/context";
import { describeError } from "@/components/proxy-hosts/lib";
import { validateNewPassword } from "@/components/users/lib";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function AccountView() {
  const { user, refreshUser } = useAuth();

  // --- profile
  const [fullName, setFullName] = useState(user?.full_name ?? "");
  const [profileError, setProfileError] = useState<string | null>(null);
  const [savingProfile, setSavingProfile] = useState(false);

  useEffect(() => {
    setFullName(user?.full_name ?? "");
  }, [user?.full_name]);

  async function saveProfile() {
    setProfileError(null);
    setSavingProfile(true);
    try {
      await users.updateMe({ full_name: fullName.trim() });
      await refreshUser();
      toast.success("Profile saved");
    } catch (err) {
      setProfileError(describeError(err).message);
    } finally {
      setSavingProfile(false);
    }
  }

  // --- password
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [savingPassword, setSavingPassword] = useState(false);

  async function changePassword() {
    const invalid = validateNewPassword(next, confirm);
    if (invalid) return setPasswordError(invalid);
    setPasswordError(null);
    setSavingPassword(true);
    try {
      await users.changeMyPassword({ current_password: current, new_password: next });
      setCurrent("");
      setNext("");
      setConfirm("");
      toast.success("Password changed");
    } catch (err) {
      setPasswordError(describeError(err).message);
    } finally {
      setSavingPassword(false);
    }
  }

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6">
      <div className="flex items-center gap-3">
        <div className="flex size-10 items-center justify-center rounded-lg bg-muted text-muted-foreground">
          <CircleUser className="size-5" />
        </div>
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Account</h2>
          <p className="text-sm text-muted-foreground">Your profile and sign-in password.</p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Profile</CardTitle>
          <CardDescription>How your name appears in the app and in the audit log.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="account-email">Email</Label>
            <Input id="account-email" value={user?.email ?? ""} readOnly disabled />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="account-name">Full name</Label>
            <Input
              id="account-name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              disabled={savingProfile}
            />
          </div>
          {profileError ? (
            <p role="alert" className="text-sm text-destructive">
              {profileError}
            </p>
          ) : null}
        </CardContent>
        <CardFooter className="justify-end">
          <Button onClick={saveProfile} disabled={savingProfile}>
            {savingProfile ? "Saving…" : "Save profile"}
          </Button>
        </CardFooter>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Password</CardTitle>
          <CardDescription>Re-enter your current password to set a new one.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="account-current">Current password</Label>
            <Input
              id="account-current"
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              disabled={savingPassword}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="account-new">New password</Label>
            <Input
              id="account-new"
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              disabled={savingPassword}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="account-confirm">Confirm new password</Label>
            <Input
              id="account-confirm"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              disabled={savingPassword}
            />
          </div>
          {passwordError ? (
            <p role="alert" className="text-sm text-destructive">
              {passwordError}
            </p>
          ) : null}
        </CardContent>
        <CardFooter className="justify-end">
          <Button onClick={changePassword} disabled={savingPassword}>
            {savingPassword ? "Saving…" : "Change password"}
          </Button>
        </CardFooter>
      </Card>
    </div>
  );
}
```

Create `frontend/src/app/(app)/account/page.tsx`:

```tsx
import type { Metadata } from "next";

import { AccountView } from "@/components/account/account-view";

export const metadata: Metadata = { title: "Account" };

export default function AccountPage() {
  return <AccountView />;
}
```

- [ ] **Step 5: Add the topbar menu item**

In `frontend/src/components/app-topbar.tsx`: add `import Link from "next/link";` and change the lucide import to `import { CircleUser, LogOut, UserCog } from "lucide-react";`. Inside `<DropdownMenuContent>`, insert between the separator and the Sign out item:

```tsx
            <DropdownMenuItem render={<Link href="/account" />}>
              <UserCog className="size-4" />
              Account
            </DropdownMenuItem>
```

(`DropdownMenuItem` wraps base-ui `MenuPrimitive.Item`, which accepts `render` — the same composition the sidebar uses for `SidebarMenuButton`.)

- [ ] **Step 6: Run tests, lint, typecheck, full frontend suite**

Run (from `frontend/`): `npx vitest run src/components/account/account-view.test.tsx && npm run lint && npm run typecheck && npm run test`
Expected: `3 passed`; everything clean; full suite green.

- [ ] **Step 7: Normalize, commit**

```bash
sed -i 's/\r$//' frontend/src/lib/auth/context.tsx frontend/src/components/app-topbar.tsx frontend/src/components/account/account-view.tsx frontend/src/components/account/account-view.test.tsx 'frontend/src/app/(app)/account/page.tsx'
git add frontend/src/lib/auth/context.tsx frontend/src/components/app-topbar.tsx frontend/src/components/account/account-view.tsx frontend/src/components/account/account-view.test.tsx 'frontend/src/app/(app)/account/page.tsx'
git commit -m "feat(account): profile and password self-service page, refreshUser in auth context

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 12: Documentation

**Files:**
- Modify: `docs/auth-api.md` (after the `POST /api/v1/users` block, ~line 57), `docs/CONVENTIONS.md` (nav line ~45 and the "Ticket sizing" paragraph ~line 152), `docs/backlog/audit-log-endpoints.md`, `README.md:41-47`

- [ ] **Step 1: `docs/auth-api.md`**

Insert after the `POST /api/v1/users` section:

```markdown
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
```

- [ ] **Step 2: `docs/CONVENTIONS.md`**

Change the `config/nav.ts` line in the tree to:

```
├── config/nav.ts             Sidebar nav (+ `adminOnly` items, `navForRole`, `utilityRoutes`)
```

Append to the "Ticket sizing" paragraph:

```markdown
Admin-only areas set `adminOnly: true` on their `NavItem`; the sidebar renders
`navForRole(user.role)` so members never see them (the API's 403 is the real
gate). Pages reached from the account menu rather than the sidebar (e.g.
`/account`) are titled via `utilityRoutes` instead of a nav entry.
```

- [ ] **Step 3: `docs/backlog/audit-log-endpoints.md`**

Under "What is missing (the deliverable)", item 2, append a sentence: `Users are covered as of the user-management change (create/update/enable/disable/delete + password events; see docs/auth-api.md#audit).`

- [ ] **Step 4: `README.md`**

After the sentence ending `to require it).` in the **Default login** paragraph, add:

```markdown
Admins manage accounts and roles at http://localhost:3000/users; everyone can
change their own password at http://localhost:3000/account.
```

- [ ] **Step 5: Normalize, commit**

```bash
sed -i 's/\r$//' docs/auth-api.md docs/CONVENTIONS.md docs/backlog/audit-log-endpoints.md README.md
git add docs/auth-api.md docs/CONVENTIONS.md docs/backlog/audit-log-endpoints.md README.md
git commit -m "docs: user management endpoints, lock-out rules, audit, nav conventions

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 13: Full gates and live verification on the compose stack

**Files:** none (verification only).

- [ ] **Step 1: Backend gates**

```bash
docker exec megoopm-test ruff check .
docker exec megoopm-test ruff format --check app/schemas/user.py app/services/user.py app/api/routes/users.py tests/test_user_schemas.py tests/test_user_service.py tests/test_users_management.py
docker exec megoopm-test python -m pytest -p no:cacheprovider
```
Expected: ruff clean on the touched files (32 *other* files already fail `ruff format --check .` on `main` — pre-existing, not in scope); pytest summary shows 0 failed and `215 passed` (179 baseline + 6 schema + 13 service + 13 admin-route + 4 self-service tests), `40 skipped`.

- [ ] **Step 2: Frontend gates**

From `frontend/`: `npm run lint && npm run typecheck && npm run test && npm run build`
Expected: all green; the build reports routes `/users` and `/account`.

- [ ] **Step 3: Rebuild the stack**

```bash
docker compose up -d --build backend frontend
for i in $(seq 1 24); do docker compose ps --format '{{.Name}} {{.Status}}' | grep -qE 'starting|unhealthy' || break; sleep 5; done
docker compose ps --format 'table {{.Name}}\t{{.Status}}'
```
Expected: `backend` and `frontend` healthy.

- [ ] **Step 4: API walk-through as the seeded admin**

```bash
API=http://localhost:8000/api/v1
TOK=$(curl -sS -H 'Content-Type: application/json' -d '{"email":"admin@example.com","password":"changeme"}' $API/auth/login | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
# create a member
curl -sS -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' -d '{"email":"m@example.com","password":"password123","role":"member"}' $API/users; echo
# self-delete must be 409
ME=$(curl -sS -H "Authorization: Bearer $TOK" $API/users/me | python -c 'import sys,json;print(json.load(sys.stdin)["id"])')
curl -sS -o /dev/null -w 'self-delete HTTP %{http_code}\n' -X DELETE -H "Authorization: Bearer $TOK" $API/users/$ME
# member: /users is 403, own password change works
MTOK=$(curl -sS -H 'Content-Type: application/json' -d '{"email":"m@example.com","password":"password123"}' $API/auth/login | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -sS -o /dev/null -w 'member list HTTP %{http_code}\n' -H "Authorization: Bearer $MTOK" $API/users
curl -sS -o /dev/null -w 'member pw change HTTP %{http_code}\n' -X PUT -H "Authorization: Bearer $MTOK" -H 'Content-Type: application/json' -d '{"current_password":"password123","new_password":"brandnew123"}' $API/users/me/password
# audit rows exist
docker compose exec -T db psql -U megoopm -d megoopm -tAc "select action, actor, meta from audit_log where object_type='user' order by id"
```
Expected: `201` body for create; `self-delete HTTP 409`; `member list HTTP 403`; `member pw change HTTP 204`; audit rows `create` (admin) and `update {"password_changed": true}` (member).

- [ ] **Step 5: UI check**

In a browser (or headless Chrome), sign in at http://localhost:3000/login as `admin@example.com` / `changeme`: the sidebar shows **Users**; `/users` lists both accounts, the admin row has the **You** badge and a disabled delete button; the account menu shows **Account** and `/account` renders both cards. Sign in as `m@example.com` / `brandnew123`: **Users** is absent from the sidebar and `/users` shows the "Couldn't load users" 403 error state. Headless screenshot for the record:

```bash
S="$HOME/AppData/Local/Temp/claude/c--Projects-megoopm/a95a330d-4c83-4554-96f2-dfe692f1f2b8/scratchpad"
"/c/Program Files/Google/Chrome/Application/chrome.exe" --headless=new --disable-gpu --no-sandbox --hide-scrollbars --window-size=1200,800 --virtual-time-budget=10000 --screenshot="$S/users-page.png" http://localhost:3000/users
```
(The headless session has no cookies, so this captures the login redirect; use a signed-in browser for the visual check and keep the screenshot as the render proof.)

- [ ] **Step 6: Clean up**

```bash
docker rm -f megoopm-test
git status --short   # expect: clean
git log --oneline -13 # expect: one commit per task above
```

Do not delete the `m@example.com` test user from the live DB unless the user asks; mention it in the hand-off.
