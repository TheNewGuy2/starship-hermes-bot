# src/bot/exit_planner/planner.py
from __future__ import annotations

import math
from typing import Optional

from starship_engine.apps.exit_planner.eta import (
    _estimate_single_target,
    _option_mark,
    _to_float,
    estimate_eta,
)
from starship_engine.apps.exit_planner.targets import (
    compute_exit_score,
    select_target_band,
)
from starship_engine.apps.exit_planner.types import (
    ExitEta,
    ExitPlan,
    ExitPlannerConfig,
    ExitPosition,
    ExitTargets,
)
from starship_engine.apps.heston.logic import HestonGateResult
from starship_engine.condor import Condor
from starship_engine.domain.types import BarFeatures


def _is_finite(val: float | None) -> bool:
    return isinstance(val, (int, float)) and math.isfinite(val)


def _position_from_condor(
    *,
    condor: Condor,
    greeks_by_symbol: dict[str, object],
    spot: float,
) -> Optional[ExitPosition]:
    def _leg(sym: str) -> Optional[object]:
        return greeks_by_symbol.get(sym)

    legs = {
        "short_put": _leg(condor.short_put.event_symbol),
        "long_put": _leg(condor.long_put.event_symbol),
        "short_call": _leg(condor.short_call.event_symbol),
        "long_call": _leg(condor.long_call.event_symbol),
    }

    if any(v is None for v in legs.values()):
        return None

    marks = {}
    for key, g in legs.items():
        mark = _option_mark(g)
        if mark is None:
            return None
        marks[key] = mark

    credit_open = (
        marks["short_put"]
        + marks["short_call"]
        - marks["long_put"]
        - marks["long_call"]
    )
    if credit_open <= 0:
        return None

    # Treat current mark as entry for candidate planning.
    mark_now = credit_open

    delta = gamma = theta = vega = 0.0
    delta_ok = gamma_ok = theta_ok = True
    for key, g in legs.items():
        sign = -1.0 if "short" in key else 1.0
        delta_val = _to_float(getattr(g, "delta", None))
        gamma_val = _to_float(getattr(g, "gamma", None))
        theta_val = _to_float(getattr(g, "theta", None))
        vega_val = _to_float(getattr(g, "vega", None))

        if delta_val is None:
            delta_ok = False
        else:
            delta += sign * delta_val
        if gamma_val is None:
            gamma_ok = False
        else:
            gamma += sign * gamma_val
        if theta_val is None:
            theta_ok = False
        else:
            theta += sign * theta_val
        if vega_val is not None:
            vega += sign * vega_val

    if not _is_finite(spot) or spot <= 0:
        return None
    if not (delta_ok and gamma_ok and theta_ok):
        return None

    return ExitPosition(
        credit_open=float(credit_open),
        mark_now=float(mark_now),
        theta_day=float(theta),
        delta=float(delta),
        gamma=float(gamma),
        vega=float(vega),
        underlying_price=float(spot),
    )


def build_exit_targets(
    *,
    features: BarFeatures,
    gate: HestonGateResult,
    vov_series: list[float],
    cfg: ExitPlannerConfig,
) -> ExitTargets:
    score_result = compute_exit_score(
        features=features,
        gate=gate,
        vov_series=vov_series,
        vov_stable_threshold=cfg.vov_stable_threshold,
        vov_window=cfg.vov_window,
    )
    return select_target_band(
        score=score_result.score,
        reasons=score_result.reasons,
        cfg=cfg,
    )


def build_exit_plan(
    *,
    features: BarFeatures,
    gate: HestonGateResult,
    greeks_by_symbol: Optional[dict[str, object]],
    condor: Optional[Condor],
    spot: Optional[float],
    vov_series: list[float],
    cfg: ExitPlannerConfig,
) -> ExitPlan:
    if not cfg.enabled or not cfg.targets_enabled:
        return ExitPlan(
            tier=None,
            score=None,
            target_band_pct=None,
            primary_target_pct=None,
            reasons=[],
            eta=ExitEta(enabled=False, reason="disabled"),
        )

    targets = build_exit_targets(
        features=features,
        gate=gate,
        vov_series=vov_series,
        cfg=cfg,
    )

    eta = ExitEta(enabled=False, reason="disabled")
    position: Optional[ExitPosition] = None
    if cfg.eta_enabled:
        if condor is None or greeks_by_symbol is None or spot is None:
            eta = ExitEta(enabled=False, reason="missing_position")
        else:
            position = _position_from_condor(
                condor=condor,
                greeks_by_symbol=greeks_by_symbol,
                spot=spot,
            )
            if position is None:
                eta = ExitEta(enabled=False, reason="missing_greeks")
            else:
                eta = estimate_eta(
                    position=position,
                    features=features,
                    vrp=gate.vrp,
                    vov_series=vov_series,
                    cfg=cfg,
                )

    if eta.enabled:
        primary = targets.primary_target_pct
        theta_per_min = position.theta_day / 390.0
        move_points = eta.assumptions.scenario_move.get("points", 0.0)

        flat = _estimate_single_target(
            v_now=position.mark_now,
            v_target=position.credit_open * (1.0 - (primary / 100.0)),
            theta_per_min=theta_per_min,
            delta=position.delta,
            gamma=position.gamma,
            dS=0.0,
            max_minutes=cfg.eta_max_minutes,
            step_seconds=cfg.eta_step_seconds,
        )
        down = _estimate_single_target(
            v_now=position.mark_now,
            v_target=position.credit_open * (1.0 - (primary / 100.0)),
            theta_per_min=theta_per_min,
            delta=position.delta,
            gamma=position.gamma,
            dS=-move_points,
            max_minutes=cfg.eta_max_minutes,
            step_seconds=cfg.eta_step_seconds,
        )
        up = _estimate_single_target(
            v_now=position.mark_now,
            v_target=position.credit_open * (1.0 - (primary / 100.0)),
            theta_per_min=theta_per_min,
            delta=position.delta,
            gamma=position.gamma,
            dS=move_points,
            max_minutes=cfg.eta_max_minutes,
            step_seconds=cfg.eta_step_seconds,
        )
        to_primary = {
            "flat": [flat, flat] if flat is not None else None,
            "down": [down, down] if down is not None else None,
            "up": [up, up] if up is not None else None,
        }
        eta = ExitEta(
            enabled=True,
            confidence=eta.confidence,
            assumptions=eta.assumptions,
            to_15_pct=eta.to_15_pct,
            to_25_pct=eta.to_25_pct,
            to_primary_pct=to_primary,
            reason=None,
        )

    return ExitPlan(
        tier=targets.tier,
        score=targets.score,
        target_band_pct=targets.target_band_pct,
        primary_target_pct=targets.primary_target_pct,
        reasons=targets.reasons,
        eta=eta,
    )
