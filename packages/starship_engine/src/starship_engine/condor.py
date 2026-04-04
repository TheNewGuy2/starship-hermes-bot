# src/bot/condor.py
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

from starship_shared.options import parse_spxw_event_symbol


@dataclass(frozen=True)
class OptionPoint:
    event_symbol: str
    right: str  # "C" or "P"
    strike: float
    delta: float
    volatility: Optional[float] = None


@dataclass(frozen=True)
class Condor:
    short_put: OptionPoint
    long_put: OptionPoint
    short_call: OptionPoint
    long_call: OptionPoint

    @property
    def width_put(self) -> float:
        return self.short_put.strike - self.long_put.strike

    @property
    def width_call(self) -> float:
        return self.long_call.strike - self.short_call.strike

    @property
    def net_short_delta(self) -> float:
        # Short put delta is typically negative; short call delta positive.
        return self.short_put.delta + self.short_call.delta


def _to_option_points(greeks_by_symbol: dict[str, object]) -> list[OptionPoint]:
    pts: list[OptionPoint] = []
    for sym, g in greeks_by_symbol.items():
        parsed = parse_spxw_event_symbol(sym)
        if not parsed:
            continue

        d = getattr(g, "delta", None)
        try:
            d = float(d)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(d):
            continue

        vol = getattr(g, "volatility", None)
        try:
            vol = float(vol)
        except (TypeError, ValueError):
            vol = None

        pts.append(
            OptionPoint(
                event_symbol=sym,
                right=parsed.right,
                strike=float(parsed.strike),
                delta=float(d),
                volatility=vol,
            )
        )
    return pts


def _nearest_strike(
    points: list[OptionPoint], right: str, target_strike: float
) -> Optional[OptionPoint]:
    same = [p for p in points if p.right == right]
    if not same:
        return None
    return min(same, key=lambda p: abs(p.strike - target_strike))


def _pick_by_target_delta(
    points: list[OptionPoint], right: str, target_delta: float
) -> Optional[OptionPoint]:
    """
    Picks the option whose delta is closest to target_delta.
    For puts, deltas are typically negative; for calls positive.
    """
    same = [p for p in points if p.right == right]
    if not same:
        return None
    # Use absolute distance to the target
    return min(same, key=lambda p: abs(p.delta - target_delta))


def pick_condor_20delta_20wide(
    greeks_by_symbol: dict[str, object],
    *,
    target_abs_delta: float = 0.20,
    wing_width: float = 20.0,
    delta_neutral: bool = True,
    neutral_tol: float = 0.03,
    adjust_step: float = 0.02,
    min_abs_delta: float = 0.10,
    max_abs_delta: float = 0.35,
) -> Optional[Condor]:
    """
    Build a 0DTE SPXW iron condor:
      - short put near -target_abs_delta
      - short call near +target_abs_delta
      - $wing_width wide wings, snapped to nearest listed strike
      - optional delta-neutral adjustment

    Returns None if insufficient greeks/options.
    """
    pts = _to_option_points(greeks_by_symbol)
    puts = [p for p in pts if p.right == "P"]
    calls = [p for p in pts if p.right == "C"]
    if len(puts) < 4 or len(calls) < 4:
        return None

    absd_put = target_abs_delta
    absd_call = target_abs_delta

    # Initial picks
    sp = _pick_by_target_delta(pts, "P", -absd_put)
    sc = _pick_by_target_delta(pts, "C", +absd_call)
    if not sp or not sc:
        return None

    # Optional delta-neutral adjustment: nudge one side in small steps
    if delta_neutral:
        # Iterate a few steps max to avoid runaway
        for _ in range(10):
            net = sp.delta + sc.delta
            if abs(net) <= neutral_tol:
                break

            if net < -neutral_tol:
                # Too negative => move call closer (higher +delta)
                absd_call = min(max_abs_delta, absd_call + adjust_step)
                sc2 = _pick_by_target_delta(pts, "C", +absd_call)
                if sc2:
                    sc = sc2
                else:
                    break

            elif net > neutral_tol:
                # Too positive => move put closer (more negative delta)
                absd_put = min(max_abs_delta, absd_put + adjust_step)
                sp2 = _pick_by_target_delta(pts, "P", -absd_put)
                if sp2:
                    sp = sp2
                else:
                    break

            if absd_put > max_abs_delta or absd_call > max_abs_delta:
                break

    # Wings: target strikes, then snap to nearest listed strike
    lp_target = sp.strike - wing_width
    lc_target = sc.strike + wing_width

    lp = _nearest_strike(pts, "P", lp_target)
    lc = _nearest_strike(pts, "C", lc_target)
    if not lp or not lc:
        return None

    # Sanity: ensure wings are on correct side
    if lp.strike >= sp.strike:
        # if snapping caused inversion, try force exact target by picking nearest below
        below = [p for p in pts if p.right == "P" and p.strike < sp.strike]
        if not below:
            return None
        lp = min(below, key=lambda p: abs(p.strike - lp_target))

    if lc.strike <= sc.strike:
        above = [p for p in pts if p.right == "C" and p.strike > sc.strike]
        if not above:
            return None
        lc = min(above, key=lambda p: abs(p.strike - lc_target))

    return Condor(short_put=sp, long_put=lp, short_call=sc, long_call=lc)
