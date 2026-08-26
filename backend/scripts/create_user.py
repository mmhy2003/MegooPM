"""Create a user (typically the first admin) from the command line.

Solves the bootstrap chicken-and-egg: creating users is an admin-only API, so
the very first admin must be seeded out-of-band. Run from ``backend``::

    # Explicit flags
    python -m scripts.create_user --email admin@example.com --password s3cret --role admin

    # Or from FIRST_ADMIN_EMAIL / FIRST_ADMIN_PASSWORD in the environment / .env
    python -m scripts.create_user --from-env

Idempotent: if the email already exists the script reports it and exits 0
without modifying the existing account.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.user import UserRole
from app.services import user as user_service


async def _create(email: str, password: str, full_name: str, role: UserRole) -> int:
    async with SessionLocal() as session:
        existing = await user_service.get_by_email(session, email)
        if existing is not None:
            print(f"User {email!r} already exists (id={existing.id}); nothing to do.")
            return 0
        user = await user_service.create_user(
            session,
            email=email,
            password=password,
            full_name=full_name,
            role=role,
        )
        print(f"Created {role.value} user {user.email!r} (id={user.id}).")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a MegooPM user.")
    parser.add_argument("--email")
    parser.add_argument("--password")
    parser.add_argument("--full-name", default="")
    parser.add_argument(
        "--role",
        choices=[r.value for r in UserRole],
        default=UserRole.admin.value,
    )
    parser.add_argument(
        "--from-env",
        action="store_true",
        help="Read email/password from FIRST_ADMIN_EMAIL / FIRST_ADMIN_PASSWORD.",
    )
    args = parser.parse_args(argv)

    if args.from_env:
        email = settings.first_admin_email
        password = settings.first_admin_password
        if not email or not password:
            parser.error(
                "--from-env requires FIRST_ADMIN_EMAIL and FIRST_ADMIN_PASSWORD to be set."
            )
    else:
        email, password = args.email, args.password
        if not email or not password:
            parser.error("--email and --password are required (or use --from-env).")

    return asyncio.run(_create(email.lower(), password, args.full_name, UserRole(args.role)))


if __name__ == "__main__":
    sys.exit(main())
