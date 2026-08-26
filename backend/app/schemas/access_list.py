"""Pydantic schemas for access lists (basic-auth + IP allow/deny).

An :class:`~app.models.access_list.AccessList` bundles two independent gates a
proxy host can enforce: HTTP basic-auth users and IP allow/deny rules. These
schemas are the versioned API contract the frontend consumes.

Passwords are **write-only**: they are accepted on create/update, hashed into the
nginx-native ``$apr1$`` format by the service layer, and never returned. Client
addresses are validated as an IP, a CIDR range, or the literal ``all`` at the API
boundary so a malformed rule is rejected with a 422 rather than breaking
``nginx -t`` at reload time.
"""

from __future__ import annotations

import ipaddress
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import AccessListDirective


def _validate_address(value: str) -> str:
    """Accept ``all``, a bare IP, or a CIDR network; return it normalised."""
    stripped = value.strip()
    if not stripped:
        raise ValueError("address must not be empty")
    if stripped.lower() == "all":
        return "all"
    try:
        if "/" in stripped:
            # strict=False tolerates host bits set (e.g. 10.0.0.5/24).
            return str(ipaddress.ip_network(stripped, strict=False))
        return str(ipaddress.ip_address(stripped))
    except ValueError as exc:
        raise ValueError(f"invalid IP or CIDR: {value!r}") from exc


def _validate_username(value: str) -> str:
    """htpasswd usernames may not contain ``:`` or whitespace."""
    stripped = value.strip()
    if not stripped:
        raise ValueError("username must not be empty")
    if ":" in stripped or any(c.isspace() for c in stripped):
        raise ValueError("username must not contain ':' or whitespace")
    return stripped


# --- Basic-auth users ------------------------------------------------------


class AccessListAuthCreate(BaseModel):
    """A basic-auth credential to add to an access list (password write-only)."""

    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, description="Plaintext; stored hashed, never returned")

    @field_validator("username")
    @classmethod
    def _clean_username(cls, value: str) -> str:
        return _validate_username(value)


class AccessListAuthUpdate(BaseModel):
    """Reset a basic-auth user's password."""

    password: str = Field(min_length=1)


class AccessListAuthRead(BaseModel):
    """Public representation of a basic-auth user (no credential material)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    created_at: datetime
    updated_at: datetime


# --- IP client rules -------------------------------------------------------


class AccessListClientCreate(BaseModel):
    """An allow/deny rule for an IP address, CIDR range, or ``all``."""

    address: str = Field(min_length=1, max_length=255, description="IP, CIDR, or 'all'")
    directive: AccessListDirective = Field(description="allow or deny")

    @field_validator("address")
    @classmethod
    def _clean_address(cls, value: str) -> str:
        return _validate_address(value)


class AccessListClientUpdate(BaseModel):
    """Partial update of a client rule."""

    address: str | None = Field(default=None, min_length=1, max_length=255)
    directive: AccessListDirective | None = None

    @field_validator("address")
    @classmethod
    def _clean_address(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_address(value)


class AccessListClientRead(BaseModel):
    """Public representation of an IP client rule."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    address: str
    directive: AccessListDirective
    created_at: datetime
    updated_at: datetime


# --- Access list -----------------------------------------------------------


class AccessListBase(BaseModel):
    """Fields shared by access-list read/write schemas."""

    name: str = Field(min_length=1, max_length=255, description="Human-readable name")
    satisfy_any: bool = Field(
        default=False,
        description="Pass if EITHER gate (auth OR ip) is satisfied; false requires both",
    )
    pass_auth: bool = Field(
        default=False, description="Forward the Authorization header to the upstream"
    )


class AccessListCreate(AccessListBase):
    """Payload to create an access list, optionally seeding users and rules inline."""

    auth_users: list[AccessListAuthCreate] = Field(default_factory=list)
    clients: list[AccessListClientCreate] = Field(default_factory=list)

    @field_validator("auth_users")
    @classmethod
    def _unique_usernames(cls, value: list[AccessListAuthCreate]) -> list[AccessListAuthCreate]:
        seen = {u.username for u in value}
        if len(seen) != len(value):
            raise ValueError("duplicate usernames within the access list")
        return value


class AccessListUpdate(BaseModel):
    """Partial update of an access list's own attributes (not its users/rules)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    satisfy_any: bool | None = None
    pass_auth: bool | None = None


class AccessListRead(AccessListBase):
    """Public representation of an access list, including users and rules."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    auth_users: list[AccessListAuthRead] = Field(default_factory=list)
    client_rules: list[AccessListClientRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


__all__ = [
    "AccessListAuthCreate",
    "AccessListAuthRead",
    "AccessListAuthUpdate",
    "AccessListBase",
    "AccessListClientCreate",
    "AccessListClientRead",
    "AccessListClientUpdate",
    "AccessListCreate",
    "AccessListRead",
    "AccessListUpdate",
]
