# src/bot/indicators.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Deque, Optional, Tuple
from collections import deque
import math

import numpy as np


@dataclass(frozen=True)
class CandleBar:
    """
    Minimal candle bar representation for indicator calculations.
    Values should be floats (price units).
    time_ms is optional (dxFeed Candle has .time in ms).
    """
    open: float
    high: float
    low: float
    close: float
    time_ms: Optional[int] = None


class CandleBuffer:
    """
    Rolling buffer of CandleBar objects + common derived series.
    Designed for 5-minute bars, but works for any fixed interval.
    """
    def __init__(self, maxlen: int = 300):
        self._bars: Deque[CandleBar] = deque(maxlen=maxlen)

    def __len__(self) -> int:
        return len(self._bars)

    def append(self, bar: CandleBar) -> None:
        self._bars.append(bar)

    def bars(self) -> Tuple[CandleBar, ...]:
        return tuple(self._bars)

    def closes(self) -> np.ndarray:
        return np.array([b.close for b in self._bars], dtype=float)

    def highs(self) -> np.ndarray:
        return np.array([b.high for b in self._bars], dtype=float)

    def lows(self) -> np.ndarray:
        return np.array([b.low for b in self._bars], dtype=float)

    def last(self) -> Optional[CandleBar]:
        return self._bars[-1] if self._bars else None


def ema(series: np.ndarray, length: int) -> np.ndarray:
    """
    Exponential moving average.
    Returns an array same length as series, with NaN until enough points exist.
    """
    if length <= 0:
        raise ValueError("EMA length must be > 0")
    series = np.asarray(series, dtype=float)
    n = series.size
    out = np.full(n, np.nan, dtype=float)
    if n == 0:
        return out

    alpha = 2.0 / (length + 1.0)

    # Start EMA after we have 'length' points using SMA seed.
    if n < length:
        return out

    seed = np.mean(series[:length])
    out[length - 1] = seed
    prev = seed
    for i in range(length, n):
        prev = alpha * series[i] + (1.0 - alpha) * prev
        out[i] = prev
    return out


def true_range(high: float, low: float, prev_close: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, length: int) -> np.ndarray:
    """
    Average True Range (Wilder-style smoothing).
    Returns an array same length as inputs, NaN until enough points exist.
    """
    if length <= 0:
        raise ValueError("ATR length must be > 0")

    highs = np.asarray(highs, dtype=float)
    lows = np.asarray(lows, dtype=float)
    closes = np.asarray(closes, dtype=float)

    n = closes.size
    out = np.full(n, np.nan, dtype=float)
    if n == 0:
        return out
    if not (highs.size == lows.size == closes.size):
        raise ValueError("highs, lows, closes must have same length")

    # True range starts from index 1 (needs prev close)
    tr = np.full(n, np.nan, dtype=float)
    for i in range(1, n):
        tr[i] = true_range(highs[i], lows[i], closes[i - 1])

    # Need at least length TR values (which effectively means length+1 closes)
    # We'll seed at index = length using SMA of TR[1:length+1]
    seed_idx = length
    if n <= seed_idx:
        return out

    seed = np.nanmean(tr[1:seed_idx + 1])
    out[seed_idx] = seed
    prev = seed
    alpha = 1.0 / length  # Wilder smoothing

    for i in range(seed_idx + 1, n):
        if np.isnan(tr[i]):
            continue
        prev = (prev * (length - 1) + tr[i]) / length
        out[i] = prev

    return out


def log_returns(closes: np.ndarray) -> np.ndarray:
    """
    Log returns: ln(C_t / C_{t-1})
    Returns length n-1 array (same as np.diff).
    """
    closes = np.asarray(closes, dtype=float)
    if closes.size < 2:
        return np.array([], dtype=float)
    # guard against non-positive values
    if np.any(closes <= 0):
        return np.array([], dtype=float)
    return np.diff(np.log(closes))


def annualized_realized_vol(log_rets: np.ndarray, bars_per_day: int = 78, trading_days: int = 252) -> float:
    """
    Annualized realized volatility from log returns.
    Default annualization assumes 5m bars in regular session: ~78 bars/day.
    """
    log_rets = np.asarray(log_rets, dtype=float)
    if log_rets.size < 2:
        return float("nan")
    sigma = float(np.std(log_rets, ddof=1))
    return sigma * math.sqrt(trading_days * bars_per_day)


def realized_vol_windows_from_closes(
    closes: np.ndarray,
    *,
    bars_30m: int = 6,
    bars_60m: int = 12,
    bars_90m: int = 18,
    bars_per_day: int = 78,
) -> dict[str, float]:
    """
    Convenience helper for your Heston-style module:
      RV_30m, RV_60m, RV_90m from the last N 5m bars.

    Requires at least (bars_90m + 1) closes.
    """
    closes = np.asarray(closes, dtype=float)
    out = {"rv_30m": float("nan"), "rv_60m": float("nan"), "rv_90m": float("nan")}
    if closes.size < (bars_90m + 1):
        return out

    # Returns arrays are one shorter than closes, so for N bars you need N returns => N+1 closes.
    rets_30 = log_returns(closes[-(bars_30m + 1):])
    rets_60 = log_returns(closes[-(bars_60m + 1):])
    rets_90 = log_returns(closes[-(bars_90m + 1):])

    out["rv_30m"] = annualized_realized_vol(rets_30, bars_per_day=bars_per_day)
    out["rv_60m"] = annualized_realized_vol(rets_60, bars_per_day=bars_per_day)
    out["rv_90m"] = annualized_realized_vol(rets_90, bars_per_day=bars_per_day)
    return out


def vov_proxy_from_returns(log_rets: np.ndarray) -> float:
    """
    Simple vol-of-vol proxy: std dev of absolute returns.
    Useful as Heston 'sigma' proxy.
    """
    log_rets = np.asarray(log_rets, dtype=float)
    if log_rets.size < 5:
        return float("nan")
    return float(np.std(np.abs(log_rets), ddof=1))


def close_near_ema(
    close: float,
    ema_value: float,
    *,
    atr_value: Optional[float] = None,
    atr_mult: float = 0.15,
    pct_tolerance: Optional[float] = None,
) -> bool:
    """
    Decide if close is "at" EMA.

    Prefer ATR-based tolerance for 0DTE robustness:
      abs(close - ema) <= atr_mult * atr

    Or use pct_tolerance (e.g. 0.001 for 0.1%):
      abs(close - ema) <= pct_tolerance * close
    """
    if not (isinstance(close, (int, float)) and isinstance(ema_value, (int, float))):
        return False
    if close <= 0 or ema_value <= 0:
        return False

    diff = abs(close - ema_value)

    if atr_value is not None and isinstance(atr_value, (int, float)) and atr_value > 0:
        return diff <= atr_mult * atr_value

    if pct_tolerance is not None and pct_tolerance > 0:
        return diff <= pct_tolerance * close

    # no tolerance provided => can't decide
    return False
