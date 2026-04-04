from __future__ import annotations

from pydantic import BaseModel


class ContextOvernightSettings(BaseModel):
    enabled: bool = False
