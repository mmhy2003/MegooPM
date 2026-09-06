"""Payloads for the API-key routes.

:class:`ApiKeyCreated` is the only schema in the codebase that carries a secret,
and exactly one route returns it, once. Everything else reads
:class:`ApiKeyRead`, which cannot express one.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiKeyRead(BaseModel):
    """One key. Never the token, and never its digest."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    token_prefix: str
    enabled: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class ApiKeyCreated(ApiKeyRead):
    """The 201 body. The only time the token exists outside the caller."""

    token: str


class ApiKeyCreate(BaseModel):
    """Name it, and choose whether it should stop working on its own."""

    name: str = Field(min_length=1, max_length=64, description="What this key is for")
    #: None means never — a deliberate choice, which is why the UI offers it last.
    expires_at: datetime | None = Field(
        default=None, description="When the key stops working; null for never"
    )

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Give the key a name.")
        return cleaned

    @field_validator("expires_at")
    @classmethod
    def _must_be_future(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        # A client may send a naive timestamp; read it as UTC rather than
        # raising TypeError on the comparison.
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        if aware <= datetime.now(UTC):
            raise ValueError("Choose an expiry in the future, or no expiry.")
        return aware


class ApiKeyUpdate(BaseModel):
    """Only ``enabled``.

    A key's name and expiry are fixed at creation, so an audit row keeps meaning
    what it said when it was written.
    """

    enabled: bool


__all__ = ["ApiKeyCreate", "ApiKeyCreated", "ApiKeyRead", "ApiKeyUpdate"]
