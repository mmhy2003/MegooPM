"""Schemas for Live logs (the WebSocket's frames are plain JSON, not modelled)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LiveTicket(BaseModel):
    """What opens the Live logs WebSocket: pass ``ticket`` as ``?ticket=``."""

    ticket: str
    expires_in: int = Field(description="Seconds the ticket stays valid for.")
