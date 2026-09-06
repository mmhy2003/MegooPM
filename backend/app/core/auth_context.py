"""Which credential authenticated the request in flight.

One ``ContextVar``, set by ``get_current_user`` on *every* authenticated
request — to ``None`` for a browser session, to the key for an API key. Setting
it on both paths is what makes a stale value impossible: an asyncio task
inherits a copy of the enclosing context, so a value left behind by an earlier
request on the same worker would otherwise still be readable.

Two things read it. :func:`app.services.audit.record_audit` turns it into the
actor string, so an audit row names the key that made the change without 24 call
sites having to remember to pass it. ``require_session_user`` refuses it, so a
key cannot manage accounts or users.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AuthKey:
    """The API key that authenticated this request. Never the token."""

    id: int
    name: str


current_api_key: ContextVar[AuthKey | None] = ContextVar("current_api_key", default=None)


__all__ = ["AuthKey", "current_api_key"]
