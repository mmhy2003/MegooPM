"""What each common HTTP status is answered with.

The API always speaks in the full set of eight: the settings card renders one
row per code and saves them together, so a partial write would leave the
operator guessing which rows took effect.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import ErrorPageMode
from app.models.error_page import ERROR_CODES


class ErrorPageBase(BaseModel):
    code: int = Field(description="One of the eight codes MegooPM brands")
    mode: ErrorPageMode
    custom_page_id: int | None = Field(
        default=None, description="Page served when the mode is 'custom_page'"
    )

    @model_validator(mode="after")
    def _coherent(self) -> ErrorPageBase:
        """Mirror the DB constraint so the API answers 422, not a 500."""
        if self.code not in ERROR_CODES:
            raise ValueError(f"{self.code} is not one of the codes MegooPM brands.")
        if self.mode is ErrorPageMode.custom_page and self.custom_page_id is None:
            raise ValueError(f"Choose a page for {self.code}, or use the MegooPM page.")
        if self.mode is ErrorPageMode.default and self.custom_page_id is not None:
            raise ValueError(f"The MegooPM page for {self.code} takes no page of its own.")
        return self


class ErrorPageRead(ErrorPageBase):
    """One code's effective setting. A code with no row reads as 'default'."""

    model_config = ConfigDict(from_attributes=True)


class ErrorPageUpdate(ErrorPageBase):
    """One row of a whole-set write."""


__all__ = ["ErrorPageRead", "ErrorPageUpdate"]
