"""Access lists: HTTP basic-auth users and IP client rules.

An access list bundles two independent gates that a proxy host can apply:
basic-auth credentials (:class:`AccessListAuth`) and IP allow/deny rules
(:class:`AccessListClient`). ``satisfy_any`` controls whether passing *either*
gate is sufficient or *both* are required.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import AccessListDirective
from app.models.mixins import IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.proxy_host import ProxyHost


class AccessList(IdMixin, TimestampMixin, Base):
    """A reusable authorization policy applied to proxy hosts."""

    __tablename__ = "access_lists"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Satisfy ANY gate (auth OR ip) vs. ALL gates.
    satisfy_any: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Forward the basic-auth header to the upstream instead of stripping it.
    pass_auth: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    auth_users: Mapped[list[AccessListAuth]] = relationship(
        back_populates="access_list",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    client_rules: Mapped[list[AccessListClient]] = relationship(
        back_populates="access_list",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    proxy_hosts: Mapped[list[ProxyHost]] = relationship(back_populates="access_list")


class AccessListAuth(IdMixin, TimestampMixin, Base):
    """A basic-auth username/password (hashed) within an access list."""

    __tablename__ = "access_list_auth"
    __table_args__ = (
        UniqueConstraint("access_list_id", "username", name="access_list_auth_username"),
    )

    access_list_id: Mapped[int] = mapped_column(
        ForeignKey("access_lists.id", ondelete="CASCADE"), nullable=False, index=True
    )
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    # Hashed credential only — never store plaintext.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    access_list: Mapped[AccessList] = relationship(back_populates="auth_users")


class AccessListClient(IdMixin, TimestampMixin, Base):
    """An IP/CIDR allow or deny rule within an access list."""

    __tablename__ = "access_list_clients"

    access_list_id: Mapped[int] = mapped_column(
        ForeignKey("access_lists.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # IP address, CIDR range, or the literal "all".
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    directive: Mapped[AccessListDirective] = mapped_column(
        Enum(
            AccessListDirective,
            name="access_list_directive",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )

    access_list: Mapped[AccessList] = relationship(back_populates="client_rules")


__all__ = ["AccessList", "AccessListAuth", "AccessListClient"]
