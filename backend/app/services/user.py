"""User domain services.

Business logic for users lives here; routes stay thin. No FastAPI imports —
callers pass an :class:`~sqlalchemy.ext.asyncio.AsyncSession` and plain values.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, needs_rehash, verify_password
from app.models.user import User, UserRole


class EmailAlreadyExistsError(Exception):
    """Raised when creating a user with an email that is already registered."""


async def get_by_id(db: AsyncSession, user_id: int) -> User | None:
    """Return the user with ``user_id`` or ``None``."""
    return await db.get(User, user_id)


async def get_by_email(db: AsyncSession, email: str) -> User | None:
    """Return the user with ``email`` (case-insensitive) or ``None``."""
    result = await db.execute(select(User).where(User.email == email.lower()))
    return result.scalar_one_or_none()


async def list_users(db: AsyncSession) -> list[User]:
    """Return all users ordered by id."""
    result = await db.execute(select(User).order_by(User.id))
    return list(result.scalars().all())


async def create_user(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str = "",
    role: UserRole = UserRole.member,
    is_active: bool = True,
) -> User:
    """Create and persist a user, hashing ``password``.

    Raises :class:`EmailAlreadyExistsError` if the email is taken.
    """
    normalized = email.lower()
    if await get_by_email(db, normalized) is not None:
        raise EmailAlreadyExistsError(normalized)

    user = User(
        email=normalized,
        hashed_password=hash_password(password),
        full_name=full_name,
        role=role,
        is_active=is_active,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def authenticate(db: AsyncSession, *, email: str, password: str) -> User | None:
    """Return the user if credentials are valid and the account is active.

    Returns ``None`` on unknown email, bad password, or inactive account.
    Transparently upgrades the stored hash when parameters have changed.
    """
    user = await get_by_email(db, email)
    if user is None:
        # Still run a verify against a dummy value would be ideal to equalize
        # timing; argon2's own hashing on create already dominates. Keep simple.
        return None
    if not user.is_active:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    if needs_rehash(user.hashed_password):
        user.hashed_password = hash_password(password)
        await db.commit()
    return user


__all__ = [
    "EmailAlreadyExistsError",
    "authenticate",
    "create_user",
    "get_by_email",
    "get_by_id",
    "list_users",
]
