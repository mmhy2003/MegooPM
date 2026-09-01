"""Pydantic schemas for custom pages (reusable HTML response bodies).

The index and the detail view deliberately differ: :class:`CustomPageSummary`
reports a byte count where :class:`CustomPageRead` carries the document itself,
so listing twenty pages does not ship twenty full HTML sources (each of which
may hold megabytes of base64 image data) to render a table.

``html`` is capped at :data:`MAX_HTML_BYTES`. Embedding an image as a ``data:``
URI inflates it by roughly a third, so an uncapped field grows quickly; the cap
is enforced on the encoded byte length rather than the character count, because
that is what actually lands in the database and on the wire.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

MAX_HTML_BYTES = 2 * 1024 * 1024


def _validate_html(value: str) -> str:
    encoded = len(value.encode("utf-8"))
    if encoded > MAX_HTML_BYTES:
        raise ValueError(
            f"html is {encoded} bytes; the maximum is {MAX_HTML_BYTES} "
            "(embedded images count toward this)"
        )
    return value


def _validate_name(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("name must not be empty")
    return stripped


class CustomPageBase(BaseModel):
    """Fields shared by the create and update payloads."""

    name: str = Field(min_length=1, max_length=255, description="Human-readable name")
    description: str = Field(default="", max_length=1000)
    html: str = Field(default="", description="The full HTML document")


class CustomPageCreate(CustomPageBase):
    """Payload to create a page. ``html`` may be empty and filled in later."""

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        return _validate_name(value)

    @field_validator("html")
    @classmethod
    def _check_html(cls, value: str) -> str:
        return _validate_html(value)


class CustomPageUpdate(BaseModel):
    """Partial update; omitted fields are left as they are."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    html: str | None = None

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        return None if value is None else _validate_name(value)

    @field_validator("html")
    @classmethod
    def _check_html(cls, value: str | None) -> str | None:
        return None if value is None else _validate_html(value)


def _encoded_size(html: str) -> int:
    """Bytes the document occupies once encoded — what the cap measures."""
    return len(html.encode("utf-8"))


class CustomPageSummary(BaseModel):
    """Index representation: a byte count in place of the document itself."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    size_bytes: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_page(cls, page: object) -> CustomPageSummary:
        """Build a summary from an ORM row, measuring its html on the way past."""
        return cls(
            id=page.id,  # type: ignore[attr-defined]
            name=page.name,  # type: ignore[attr-defined]
            description=page.description,  # type: ignore[attr-defined]
            size_bytes=_encoded_size(page.html),  # type: ignore[attr-defined]
            created_at=page.created_at,  # type: ignore[attr-defined]
            updated_at=page.updated_at,  # type: ignore[attr-defined]
        )


class CustomPageRead(BaseModel):
    """Detail representation: the full document, for the editor."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    html: str
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def size_bytes(self) -> int:
        """Matches the summary's field so both views report the same weight."""
        return _encoded_size(self.html)


__all__ = [
    "MAX_HTML_BYTES",
    "CustomPageBase",
    "CustomPageCreate",
    "CustomPageRead",
    "CustomPageSummary",
    "CustomPageUpdate",
]
