"""User domain services.

Business logic for users lives here; routes stay thin. No FastAPI imports —
callers pass an :class:`~sqlalchemy.ext.asyncio.AsyncSession` and plain values.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, needs_rehash, verify_password
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)

# The dev-compose default password. Seeding it is allowed (it is what makes a
# fresh `docker compose up` loggable-into) but must be loud in the startup log.
_WELL_KNOWN_DEFAULT_PASSWORD = "changeme"


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


async def _any_user_exists(db: AsyncSession) -> bool:
    result = await db.execute(select(User.id).limit(1))
    return result.first() is not None


async def ensure_first_admin(
    db: AsyncSession, *, email: str | None, password: str | None
) -> User | None:
    """Seed the initial admin on a fresh install.

    Creates ``email`` as an active :attr:`UserRole.admin` only when BOTH
    credentials are given **and no user exists at all**. This is an
    initial-setup step, not an "ensure this account exists" rule: once any
    user is present (including after the operator deletes or renames the
    seeded one) it does nothing, so a well-known default login can never be
    resurrected on a configured system.

    Returns the created user, or ``None`` when nothing was seeded. Two nodes
    booting an empty database at once may race here; the loser raises
    :class:`EmailAlreadyExistsError` (or an integrity error), which startup
    treats as a benign "already seeded".
    """
    if not email or not password:
        return None
    if await _any_user_exists(db):
        return None

    user = await create_user(db, email=email, password=password, role=UserRole.admin)
    if password == _WELL_KNOWN_DEFAULT_PASSWORD:
        logger.warning(
            "Seeded initial admin %r with the well-known default password — "
            "change it after first login.",
            user.email,
        )
    else:
        logger.info("Seeded initial admin %r.", user.email)
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
    "ensure_first_admin",
    "get_by_email",
    "get_by_id",
    "list_users",
]
