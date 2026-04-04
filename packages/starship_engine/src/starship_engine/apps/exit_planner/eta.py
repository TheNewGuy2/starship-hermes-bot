# src/bot/exit_planner/eta.py
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import time
from typing import Optional
from zoneinfo import ZoneInfo

from starship_engine.apps.exit_planner.types import (
    ExitAssumptions,
    ExitEta,
    ExitPlannerConfig,
    ExitPosition,
)
from starship_engine.domain.types import BarFeatures


@dataclass(frozen=True)
class EtaConfidence:
    level: str
    reason: str


def _is_finite(val: float | None) -> bool:
    return isinstance(val, (int, float)) and math.isfinite(val)


def _to_float(val: object) -> Optional[float]:
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _option_mark(g) -> Optional[float]:
    for name in (
        "mark",
        "mark_price",
        "mid",
        "mid_price",
        "midPrice",
        "last",
        "close",
        "price",
    ):
        val = _to_float(getattr(g, name, None))
        if val is not None and val > 0:
            return val

    bid = _to_float(getattr(g, "bid_price", None))
    ask = _to_float(getattr(g, "ask_price", None))
    if bid is None:
        bid = _to_float(getattr(g, "bid", None))
    if ask is None:
        ask = _to_float(getattr(g, "ask", None))
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2.0

    return None


def _compute_expected_move(
    *,
    spot: float,
    iv_proxy: float,
    minutes: int,
) -> float:
    minutes_per_year = 252 * 390
    return spot * iv_proxy * math.sqrt(minutes / minutes_per_year)


def _estimate_single_target(
    *,
    v_now: float,
    v_target: float,
    theta_per_min: float,
    delta: float,
    gamma: float,
    dS: float,
    max_minutes: int,
    step_seconds: int,
) -> Optional[int]:
    if v_now <= v_target:
        return 0

    t_min = 0.0
    step_min = step_seconds / 60.0

    # delta/gamma impact is applied as a static bump per scenario
    delta_gamma = delta * dS + 0.5 * gamma * dS * dS

    while t_min <= max_minutes:
        v_est = v_now - theta_per_min * t_min + delta_gamma
        if v_est <= v_target:
            return int(round(t_min))
        t_min += step_min

    return None


def _build_range(
    flat: Optional[int],
    down: Optional[int],
    up: Optional[int],
) -> dict[str, Optional[list[int]]]:
    def _rng(val: Optional[int]) -> Optional[list[int]]:
        if val is None:
            return None
        return [val, val]

    return {
        "flat": _rng(flat),
        "down": _rng(down),
        "up": _rng(up),
    }


def _confidence(
    *,
    features: BarFeatures,
    vrp: float,
    vov_series: list[float],
    vov_window: int,
    vov_stable_threshold: float,
) -> EtaConfidence:
    vov_low = _is_finite(features.vov) and features.vov <= vov_stable_threshold
    vov_falling = False
    if len(vov_series) >= vov_window:
        tail = vov_series[-vov_window:]
        vov_falling = all(tail[i] <= tail[i - 1] for i in range(1, len(tail)))

    dt_ny = features.dt.astimezone(ZoneInfo("America/New_York"))
    early_open = dt_ny.time() < time(10, 0)

    vrp_high = _is_finite(vrp) and vrp >= 2.0

    if vov_low and vov_falling and vrp_high and not early_open:
        return EtaConfidence(level="high", reason="VoV low/falling")
    if early_open or not vov_low:
        return EtaConfidence(level="low", reason="VoV unstable or early")
    return EtaConfidence(level="medium", reason="Mixed regime")


def estimate_eta(
    *,
    position: ExitPosition,
    features: BarFeatures,
    vrp: float,
    vov_series: list[float],
    cfg: ExitPlannerConfig,
) -> ExitEta:
    if not cfg.eta_enabled:
        return ExitEta(enabled=False, reason="disabled")

    if (
        not _is_finite(position.theta_day)
        or not _is_finite(position.delta)
        or not _is_finite(position.gamma)
    ):
        return ExitEta(enabled=False, reason="missing_greeks")

    if not _is_finite(position.credit_open) or not _is_finite(position.mark_now):
        return ExitEta(enabled=False, reason="missing_price")

    if not _is_finite(position.underlying_price) or position.underlying_price <= 0:
        return ExitEta(enabled=False, reason="missing_spot")

    if cfg.eta_step_seconds <= 0:
        return ExitEta(enabled=False, reason="invalid_step")

    theta_per_min = position.theta_day / 390.0

    if cfg.eta_scenario_mode == "fixed_points":
        move_points = float(cfg.eta_fixed_points)
        scenario_move = {
            "type": "fixed_points",
            "points": move_points,
        }
    else:
        iv_proxy = None
        if _is_finite(features.iv_atm):
            iv_proxy = float(features.iv_atm)
        elif _is_finite(features.rv60):
            iv_proxy = float(features.rv60)
        if iv_proxy is None:
            return ExitEta(enabled=False, reason="missing_iv")
        move_points = _compute_expected_move(
            spot=position.underlying_price,
            iv_proxy=iv_proxy,
            minutes=cfg.eta_expected_move_minutes,
        )
        scenario_move = {
            "type": "expected_move",
            "minutes": cfg.eta_expected_move_minutes,
            "points": move_points,
        }

    confidence = _confidence(
        features=features,
        vrp=vrp,
        vov_series=vov_series,
        vov_window=cfg.vov_window,
        vov_stable_threshold=cfg.vov_stable_threshold,
    )

    assumptions = ExitAssumptions(
        iv_const=True,
        price_scenarios=["flat", "down", "up"],
        scenario_move=scenario_move,
    )

    def _target_mark(pct: int) -> float:
        return position.credit_open * (1.0 - (pct / 100.0))

    v_now = position.mark_now
    dS = move_points

    to_15 = _estimate_single_target(
        v_now=v_now,
        v_target=_target_mark(15),
        theta_per_min=theta_per_min,
        delta=position.delta,
        gamma=position.gamma,
        dS=0.0,
        max_minutes=cfg.eta_max_minutes,
        step_seconds=cfg.eta_step_seconds,
    )
    to_15_down = _estimate_single_target(
        v_now=v_now,
        v_target=_target_mark(15),
        theta_per_min=theta_per_min,
        delta=position.delta,
        gamma=position.gamma,
        dS=-dS,
        max_minutes=cfg.eta_max_minutes,
        step_seconds=cfg.eta_step_seconds,
    )
    to_15_up = _estimate_single_target(
        v_now=v_now,
        v_target=_target_mark(15),
        theta_per_min=theta_per_min,
        delta=position.delta,
        gamma=position.gamma,
        dS=dS,
        max_minutes=cfg.eta_max_minutes,
        step_seconds=cfg.eta_step_seconds,
    )

    to_25 = _estimate_single_target(
        v_now=v_now,
        v_target=_target_mark(25),
        theta_per_min=theta_per_min,
        delta=position.delta,
        gamma=position.gamma,
        dS=0.0,
        max_minutes=cfg.eta_max_minutes,
        step_seconds=cfg.eta_step_seconds,
    )
    to_25_down = _estimate_single_target(
        v_now=v_now,
        v_target=_target_mark(25),
        theta_per_min=theta_per_min,
        delta=position.delta,
        gamma=position.gamma,
        dS=-dS,
        max_minutes=cfg.eta_max_minutes,
        step_seconds=cfg.eta_step_seconds,
    )
    to_25_up = _estimate_single_target(
        v_now=v_now,
        v_target=_target_mark(25),
        theta_per_min=theta_per_min,
        delta=position.delta,
        gamma=position.gamma,
        dS=dS,
        max_minutes=cfg.eta_max_minutes,
        step_seconds=cfg.eta_step_seconds,
    )

    return ExitEta(
        enabled=True,
        confidence=confidence.level,
        assumptions=assumptions,
        to_15_pct=_build_range(to_15, to_15_down, to_15_up),
        to_25_pct=_build_range(to_25, to_25_down, to_25_up),
        to_primary_pct=None,
        reason=None,
    )
