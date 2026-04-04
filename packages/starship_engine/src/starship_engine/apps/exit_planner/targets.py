# src/bot/exit_planner/targets.py
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from starship_engine.apps.exit_planner.types import ExitPlannerConfig, ExitTargets
from starship_engine.apps.heston.logic import HestonGateResult
from starship_engine.domain.types import BarFeatures

TZ_NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ExitScoreResult:
    score: int
    reasons: list[str]
    vov_falling: bool


def _is_finite(val: float | None) -> bool:
    return isinstance(val, (int, float)) and math.isfinite(val)


def _is_midday_decay_window(dt_local: datetime) -> bool:
    dt_ny = dt_local.astimezone(TZ_NY)
    t = dt_ny.time()
    return time(10, 30) <= t <= time(13, 30)


def _vov_falling(vov_series: list[float], window: int) -> bool:
    if len(vov_series) < window:
        return False
    tail = vov_series[-window:]
    for i in range(1, len(tail)):
        if tail[i] > tail[i - 1]:
            return False
    return True


def compute_exit_score(
    *,
    features: BarFeatures,
    gate: HestonGateResult,
    vov_series: list[float],
    vov_stable_threshold: float,
    vov_window: int,
) -> ExitScoreResult:
    score = 0
    reasons: list[str] = []

    vrp = gate.vrp
    if _is_finite(vrp) and vrp >= 1.8:
        score += 1
        reasons.append(f"VRP high ({vrp:.2f})")
    if _is_finite(vrp) and vrp >= 2.5:
        score += 1
        reasons.append(f"VRP very high ({vrp:.2f})")

    vov = gate.vov
    vov_falling = _vov_falling(vov_series, vov_window)
    if vov_falling:
        score += 1
        reasons.append("VoV falling")
    elif _is_finite(vov) and vov <= vov_stable_threshold:
        score += 1
        reasons.append("VoV stable")

    rv30 = features.rv30
    rv60 = features.rv60
    if _is_finite(rv30) and _is_finite(rv60) and rv30 < rv60:
        score += 1
        reasons.append("RV30 < RV60 (compression)")

    if _is_midday_decay_window(features.dt):
        score += 1
        reasons.append("Midday decay window")

    return ExitScoreResult(score=score, reasons=reasons, vov_falling=vov_falling)


def select_target_band(
    *,
    score: int,
    reasons: list[str],
    cfg: ExitPlannerConfig,
) -> ExitTargets:
    if score >= cfg.tier_a_min_score:
        tier = "A"
        band = (cfg.tier_a_min, cfg.tier_a_max)
    elif score >= cfg.tier_b_min_score:
        tier = "B"
        band = (cfg.tier_b_min, cfg.tier_b_max)
    else:
        tier = "C"
        band = (cfg.tier_c_min, cfg.tier_c_max)

    primary = band[0]

    return ExitTargets(
        tier=tier,
        score=score,
        target_band_pct=band,
        primary_target_pct=primary,
        reasons=reasons,
    )
