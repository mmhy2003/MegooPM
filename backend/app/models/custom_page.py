"""Custom pages — reusable HTML response bodies an operator authors in the app.

A page is one self-contained HTML document. Images are embedded as base64
``data:`` URIs by the editor rather than stored alongside, so a page has no
side-car assets and can be dropped anywhere a single file is wanted.

Nothing references a page yet: authoring and managing them is the whole of this
resource. The scenarios that will eventually serve one — a CrowdSec ban page, a
catch-all for unmatched hosts — are wired separately, and until then these rows
never reach the nginx renderer.
"""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin


class CustomPage(IdMixin, TimestampMixin, Base):
    """A named HTML document stored for later reuse."""

    __tablename__ = "custom_pages"

    # Unique so the page can be picked unambiguously by name once bindings land.
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # The full document. Size is capped at the API boundary, not here — base64
    # images inflate the source ~4/3x, so an uncapped column grows fast.
    html: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")


__all__ = ["CustomPage"]
