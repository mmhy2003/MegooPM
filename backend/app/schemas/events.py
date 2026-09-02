"""What a pushed event looks like.

Deliberately tiny. An event says *what changed*, never *what it changed to*:
the client refetches through the REST path, so there is exactly one
serialisation of any domain object and no second one to drift from it.

``detail`` carries identifiers only — enough for a client to decide whether it
cares, never enough to render from.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Event(BaseModel):
    """One thing that happened, at a moment."""

    type: str
    at: datetime
    detail: dict[str, Any] = Field(default_factory=dict)


__all__ = ["Event"]
