from __future__ import annotations

from pydantic import BaseModel


class HestonSettings(BaseModel):
    enabled: bool = True
    vrp_min: float = 1.25
    vrp_max: float | None = None
    mr_max: float = 0.05
    rv60_min: float = 0.05
    vov_max: float | None = None
