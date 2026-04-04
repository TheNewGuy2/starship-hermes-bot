from __future__ import annotations

import statistics
from typing import Any, Optional

from .parse import parse_spxw_event_symbol


def greek_iv(g) -> Optional[float]:
    for attr in ("volatility", "iv", "implied_volatility"):
        v = getattr(g, attr, None)
        if v is None:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if v > 0:
            if v > 3.0:  # normalize if percent-scaled
                v = v / 100.0
            return v
    return None


def compute_iv_0dte_band(
    *,
    greeks_by_symbol: dict[str, Any],
    spot: float,
    max_each_side: int = 12,
    min_each_side: int = 3,
) -> tuple[Optional[float], Optional[float], int, int, int]:
    """
    Returns (iv_med, iv_disp, n_calls, n_puts, parse_fail)
    iv_disp = p90 - p10 as a stability metric.
    """
    if spot <= 0 or not greeks_by_symbol:
        return None, None, 0, 0, 0

    calls: list[tuple[float, float]] = []
    puts: list[tuple[float, float]] = []
    parse_fail = 0

    for sym, g in greeks_by_symbol.items():
        parsed = parse_spxw_event_symbol(sym)
        if not parsed:
            parse_fail += 1
            continue

        iv = greek_iv(g)
        if iv is None:
            continue

        diff = abs(parsed.strike - spot)
        if parsed.right == "C":
            calls.append((diff, iv))
        else:
            puts.append((diff, iv))

    calls.sort(key=lambda x: x[0])
    puts.sort(key=lambda x: x[0])

    near_calls = [iv for _, iv in calls[:max_each_side]]
    near_puts = [iv for _, iv in puts[:max_each_side]]

    if len(near_calls) < min_each_side or len(near_puts) < min_each_side:
        return None, None, len(near_calls), len(near_puts), parse_fail

    vals = sorted(near_calls + near_puts)
    iv_med = statistics.median(vals)
    p10 = vals[int(0.10 * (len(vals) - 1))]
    p90 = vals[int(0.90 * (len(vals) - 1))]
    iv_disp = p90 - p10

    return iv_med, iv_disp, len(near_calls), len(near_puts), parse_fail
