"""Static sites — serve the files in a folder under the sites mount."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.access_list import AccessList
    from app.models.certificate import Certificate

# Mirrors app.schemas.static_site.FOLDER_PATTERN: the database refuses a folder
# that could leave the sites mount even if a write skips the API schema.
FOLDER_CHECK = (
    "folder ~ '^[A-Za-z0-9_-][A-Za-z0-9._-]*(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$' "
    "AND length(folder) <= 255"
)


class StaticSite(IdMixin, TimestampMixin, Base):
    """Serves a folder of plain HTML/CSS/JS files for a set of domains."""

    __tablename__ = "static_sites"
    __table_args__ = (CheckConstraint(FOLDER_CHECK, name="folder"),)

    domain_names: Mapped[list[str]] = mapped_column(
        ARRAY(String(255)), nullable=False, default=list, server_default="{}"
    )
    # Relative to settings.sites_root; validated by the schema and FOLDER_CHECK.
    folder: Mapped[str] = mapped_column(String(255), nullable=False)
    certificate_id: Mapped[int | None] = mapped_column(
        ForeignKey("certificates.id", ondelete="SET NULL"), nullable=True, index=True
    )
    access_list_id: Mapped[int | None] = mapped_column(
        ForeignKey("access_lists.id", ondelete="SET NULL"), nullable=True, index=True
    )

    ssl_forced: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    http2_support: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    hsts_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    hsts_subdomains: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    block_exploits: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    crowdsec_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    advanced_config: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", index=True
    )

    certificate: Mapped[Certificate | None] = relationship()
    access_list: Mapped[AccessList | None] = relationship()


__all__ = ["FOLDER_CHECK", "StaticSite"]
