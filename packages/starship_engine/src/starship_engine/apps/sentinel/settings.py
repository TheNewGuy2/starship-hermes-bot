from __future__ import annotations

from pydantic import BaseModel


class SentinelSettings(BaseModel):
    enabled: bool = True
    rth_only: bool = True
    timezone: str = "America/Chicago"
    heartbeat_enabled: bool = True
    heartbeat_minutes: int = 15
    heartbeat_rth_only: bool = True
    score_thresholds: list[int] = [70, 50, 30]
    score_hysteresis: int = 5
    vrp_min: float = 1.25
    vrp_caution_min: float = 1.10
    mr_max: float = 0.05
    vov_caution: float = 0.25
    vov_hostile: float = 0.35
    vov_hysteresis: float = 0.02
    cooldown_regime_change_seconds: int = 600
    cooldown_score_cross_seconds: int = 600
    cooldown_vrp_seconds: int = 600
    cooldown_vov_seconds: int = 600
    cooldown_mr_seconds: int = 600
