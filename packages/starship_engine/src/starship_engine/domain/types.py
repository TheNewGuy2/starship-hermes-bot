from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from starship_engine.indicators import CandleBar

if TYPE_CHECKING:
    from starship_engine.apps.trend_risk.logic import TrendRiskResult


@dataclass(frozen=True)
class BarFeatures:
    dt: datetime
    bar: CandleBar
    ema21: float
    atr5: float
    rv30: float
    rv60: float
    rv90: float
    vov: float
    iv_atm: Optional[float]
    iv_disp: Optional[float]
    n_calls: int
    n_puts: int
    parse_fail: int
    latest_spx_mid: Optional[float]
    greeks_symbols: int
    touch: bool
    trend: Optional["TrendRiskResult"] = None


@dataclass(frozen=True)
class EarlyConfig:
    enabled: bool
    min_bars: int
    require_calm: bool
    require_rv30_le_rv60: bool
    vrp_min: float
    mr_max: float
    rv60_min: float
    vov_max: Optional[float]


@dataclass(frozen=True)
class EarlyDecision:
    ok: bool
    reason: str
