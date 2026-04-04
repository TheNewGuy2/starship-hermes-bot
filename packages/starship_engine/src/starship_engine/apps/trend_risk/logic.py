from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass(frozen=True)
class TrendRiskConfig:
    enabled: bool = False
    adx_len: int = 14
    di_len: int = 14
    slope_lookback: int = 3
    use_rv_modifier: bool = True
    block_threshold: int = 8
    caution_threshold: int = 6
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


@dataclass(frozen=True)
class TrendRiskComponents:
    adx_level_pts: int
    adx_slope_pts: int
    di_dom_pts: int
    stretch_pts: int
    rv_mod: int

    def as_dict(self) -> dict[str, int]:
        return {
            "adx_level_pts": self.adx_level_pts,
            "adx_slope_pts": self.adx_slope_pts,
            "di_dom_pts": self.di_dom_pts,
            "stretch_pts": self.stretch_pts,
            "rv_mod": self.rv_mod,
        }


@dataclass(frozen=True)
class TrendRiskResult:
    adx: float
    di_plus: float
    di_minus: float
    di_spread: float
    adx_slope: float
    stretch_atr: float
    risk_score: int
    risk_tier: str
    components: TrendRiskComponents

    def as_dict(self) -> dict[str, object]:
        return {
            "adx14": _to_float(self.adx),
            "di_plus14": _to_float(self.di_plus),
            "di_minus14": _to_float(self.di_minus),
            "di_spread": _to_float(self.di_spread),
            "adx_slope_3": _to_float(self.adx_slope),
            "stretch_atr": _to_float(self.stretch_atr),
            "risk_score": int(self.risk_score),
            "risk_tier": self.risk_tier,
            "components": self.components.as_dict(),
        }


def _is_finite(val: float | None) -> bool:
    return isinstance(val, (int, float)) and math.isfinite(val)


def _to_float(val: float | None) -> float | None:
    return float(val) if _is_finite(val) else None


def clamp_score(score: float) -> int:
    if score < 0:
        return 0
    if score > 10:
        return 10
    return int(round(score))


def adx_level_points(adx: float, b1: float, b2: float, b3: float, b4: float) -> int:
    if not _is_finite(adx):
        return 0
    if adx < b1:
        return 0
    if adx < b2:
        return 1
    if adx < b3:
        return 2
    if adx < b4:
        return 3
    return 4


def adx_slope_points(slope: float, low: float, high: float) -> int:
    if not _is_finite(slope):
        return 0
    if slope < low:
        return 0
    if slope < high:
        return 1
    return 2


def di_spread_points(spread: float, low: float, high: float) -> int:
    if not _is_finite(spread):
        return 0
    if spread < low:
        return 0
    if spread < high:
        return 1
    return 2


def stretch_points(stretch: float, low: float, high: float) -> int:
    if not _is_finite(stretch):
        return 0
    if stretch < low:
        return 0
    if stretch < high:
        return 1
    return 2


def rv_modifier(rv30: float, rv60: float) -> int:
    if not (_is_finite(rv30) and _is_finite(rv60)):
        return 0
    if rv30 < rv60:
        return -1
    if rv30 > rv60:
        return 1
    return 0


def _wilder_smooth_sum(series: np.ndarray, length: int) -> np.ndarray:
    n = series.size
    out = np.full(n, np.nan, dtype=float)
    if n <= length:
        return out
    seed = np.nansum(series[1 : length + 1])
    out[length] = seed
    prev = seed
    for i in range(length + 1, n):
        val = series[i]
        if not np.isfinite(val):
            out[i] = np.nan
            continue
        prev = prev - (prev / length) + val
        out[i] = prev
    return out


def _wilder_smooth_avg(
    series: np.ndarray, length: int, *, start_index: int
) -> np.ndarray:
    n = series.size
    out = np.full(n, np.nan, dtype=float)
    if n <= start_index + length - 1:
        return out
    init_slice = series[start_index : start_index + length]
    if not np.all(np.isfinite(init_slice)):
        return out
    init = float(np.mean(init_slice))
    idx = start_index + length - 1
    out[idx] = init
    prev = init
    for i in range(idx + 1, n):
        val = series[i]
        if not np.isfinite(val):
            out[i] = np.nan
            continue
        prev = (prev * (length - 1) + val) / length
        out[i] = prev
    return out


def compute_dmi_adx(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    *,
    di_len: int,
    adx_len: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    highs = np.asarray(highs, dtype=float)
    lows = np.asarray(lows, dtype=float)
    closes = np.asarray(closes, dtype=float)
    n = closes.size
    if n < max(di_len + 1, di_len + adx_len):
        nan_arr = np.full(n, np.nan, dtype=float)
        return nan_arr, nan_arr, nan_arr

    tr = np.full(n, np.nan, dtype=float)
    plus_dm = np.full(n, np.nan, dtype=float)
    minus_dm = np.full(n, np.nan, dtype=float)

    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    tr_sum = _wilder_smooth_sum(tr, di_len)
    plus_sum = _wilder_smooth_sum(plus_dm, di_len)
    minus_sum = _wilder_smooth_sum(minus_dm, di_len)

    di_plus = np.full(n, np.nan, dtype=float)
    di_minus = np.full(n, np.nan, dtype=float)
    for i in range(n):
        if np.isfinite(tr_sum[i]) and tr_sum[i] > 0:
            di_plus[i] = 100.0 * (plus_sum[i] / tr_sum[i])
            di_minus[i] = 100.0 * (minus_sum[i] / tr_sum[i])

    dx = np.full(n, np.nan, dtype=float)
    for i in range(n):
        if not (np.isfinite(di_plus[i]) and np.isfinite(di_minus[i])):
            continue
        denom = di_plus[i] + di_minus[i]
        if denom <= 0:
            continue
        dx[i] = 100.0 * abs(di_plus[i] - di_minus[i]) / denom

    adx = _wilder_smooth_avg(dx, adx_len, start_index=di_len)
    return adx, di_plus, di_minus


def compute_trend_risk(
    *,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    ema21: float,
    atr_value: float,
    rv30: float,
    rv60: float,
    cfg: TrendRiskConfig,
) -> TrendRiskResult:
    adx_series, di_plus_series, di_minus_series = compute_dmi_adx(
        highs, lows, closes, di_len=cfg.di_len, adx_len=cfg.adx_len
    )
    adx = float(adx_series[-1]) if adx_series.size else float("nan")
    di_plus = float(di_plus_series[-1]) if di_plus_series.size else float("nan")
    di_minus = float(di_minus_series[-1]) if di_minus_series.size else float("nan")
    di_spread = (
        float(abs(di_plus - di_minus))
        if _is_finite(di_plus) and _is_finite(di_minus)
        else float("nan")
    )

    adx_slope = float("nan")
    if cfg.slope_lookback > 0 and adx_series.size > cfg.slope_lookback:
        prev = adx_series[-1 - cfg.slope_lookback]
        if np.isfinite(prev) and np.isfinite(adx):
            adx_slope = float(adx - prev)

    stretch_atr = float("nan")
    if _is_finite(ema21) and _is_finite(atr_value) and atr_value > 0:
        stretch_atr = float(abs(closes[-1] - ema21) / atr_value)

    adx_level_pts = adx_level_points(
        adx, cfg.adx_b1, cfg.adx_b2, cfg.adx_b3, cfg.adx_b4
    )
    adx_slope_pts = adx_slope_points(adx_slope, cfg.adx_slope_low, cfg.adx_slope_high)
    di_dom_pts = di_spread_points(di_spread, cfg.di_spread_low, cfg.di_spread_high)
    stretch_pts = stretch_points(stretch_atr, cfg.stretch_low, cfg.stretch_high)
    rv_mod = rv_modifier(rv30, rv60) if cfg.use_rv_modifier else 0

    raw_score = adx_level_pts + adx_slope_pts + di_dom_pts + stretch_pts + rv_mod
    risk_score = clamp_score(raw_score)

    if risk_score >= cfg.block_threshold:
        risk_tier = "block"
    elif risk_score >= cfg.caution_threshold:
        risk_tier = "caution"
    else:
        risk_tier = "normal"

    return TrendRiskResult(
        adx=adx,
        di_plus=di_plus,
        di_minus=di_minus,
        di_spread=di_spread,
        adx_slope=adx_slope,
        stretch_atr=stretch_atr,
        risk_score=risk_score,
        risk_tier=risk_tier,
        components=TrendRiskComponents(
            adx_level_pts=adx_level_pts,
            adx_slope_pts=adx_slope_pts,
            di_dom_pts=di_dom_pts,
            stretch_pts=stretch_pts,
            rv_mod=rv_mod,
        ),
    )
