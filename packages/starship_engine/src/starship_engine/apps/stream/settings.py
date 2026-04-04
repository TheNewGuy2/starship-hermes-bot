from __future__ import annotations

from pydantic import BaseModel


class StreamSettings(BaseModel):
    enabled: bool = True
