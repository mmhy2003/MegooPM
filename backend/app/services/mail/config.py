"""What one send needs, and the error for when it is missing.

Holds no session and no ORM row, so the sender is trivial to fake in a test.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import SmtpSecurity


class MailNotConfigured(RuntimeError):
    """No SMTP host is configured, so nothing can be sent."""


@dataclass(frozen=True, slots=True)
class MailConfig:
    """A resolved SMTP configuration with the password already decrypted."""

    host: str | None
    port: int
    security: SmtpSecurity
    username: str | None
    password: str | None
    from_address: str | None
    from_name: str | None


__all__ = ["MailConfig", "MailNotConfigured"]
