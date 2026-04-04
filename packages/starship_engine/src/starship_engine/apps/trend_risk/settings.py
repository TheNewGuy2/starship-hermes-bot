from __future__ import annotations

from pydantic import BaseModel


class TrendRiskSettings(BaseModel):
    enabled: bool = False
    adx_len: int = 14
    di_len: int = 14
    slope_lookback: int = 3
    use_rv_modifier: bool = True
    block: int = 8
    caution: int = 6
    stretch_atr_len: int = 5
    stretch_low: float = 0.7
    stretch_high: float = 1.3
    di_spread_low: float = 6.0
    di_spread_high: float = 12.0
    adx_slope_low: float = 0.5
    adx_slope_high: float = 1.5
    adx_b1: float = 20.0
    adx_b2: float = 25.0
    adx_b3: float = 30.0
    adx_b4: float = 35.0
    policy: str = "veto_or_tier"
