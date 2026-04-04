# src/bot/sentinel_state.py
from __future__ import annotations

import math
from dataclasses import dataclass

from starship_shared.sentinel import MarketSnapshot, MarketState


@dataclass(frozen=True)
class StateParams:
    vrp_friendly_min: float = 1.25
    vrp_caution_min: float = 1.10
    mr_max: float = 0.05
    vov_caution: float = 0.25
    vov_hostile: float = 0.35


def _is_finite(val: float | None) -> bool:
    return isinstance(val, (int, float)) and math.isfinite(val)


def derive_market_state(snapshot: MarketSnapshot, params: StateParams) -> MarketState:
    score = 100
    flags: list[str] = []
    reasons: list[str] = []

    vrp = snapshot.vrp
    vov = snapshot.vov
    mr = snapshot.mr

    if _is_finite(vrp):
        if vrp < params.vrp_friendly_min:
            score -= 20
            flags.append("VRP↓")
            reasons.append("VRP below friendly")
        if vrp < params.vrp_caution_min:
            score -= 25
            flags.append("VRP↓↓")
            reasons.append("VRP below caution")
    else:
        score -= 20
        flags.append("VRP?")
        reasons.append("VRP unavailable")

    if _is_finite(vov):
        if vov > params.vov_caution:
            score -= 20
            flags.append("VoV↑")
            reasons.append("VoV above caution")
        if vov > params.vov_hostile:
            score -= 25
            flags.append("VoV↑↑")
            reasons.append("VoV above hostile")
    else:
        score -= 20
        flags.append("VoV?")
        reasons.append("VoV unavailable")

    if _is_finite(mr):
        if mr > params.mr_max:
            score -= 15
            flags.append("MRbad")
            reasons.append("Mean reversion breaking")
    else:
        score -= 10
        flags.append("MR?")
        reasons.append("MR unavailable")

    score = max(0, min(100, score))

    if score >= 70:
        label = "Short-Vol Friendly"
    elif score >= 50:
        label = "Caution"
    else:
        label = "Hostile"

    return MarketState(regime_label=label, score=score, flags=flags, reasons=reasons)
