from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from starship_engine.apps.context_overnight.logic import ContextPriors
from starship_engine.apps.heston.logic import (
    HestonGateParams,
    HestonGateResult,
    evaluate_heston_gate,
)
from starship_engine.domain.types import BarFeatures, EarlyConfig, EarlyDecision


@dataclass(frozen=True)
class DecisionResult:
    ok: bool
    reason: str
    slack_ready: bool
    day_ok: bool


def decide(
    *,
    features: BarFeatures,
    gate: HestonGateResult,
    data_ready: bool,
    slack_ready: bool,
    day_ok: bool,
    dry_run: bool,
) -> DecisionResult:
    if dry_run:
        return DecisionResult(False, "dry_run", slack_ready, day_ok)
    if not features.touch:
        return DecisionResult(False, "no_touch", slack_ready, day_ok)
    if not gate.ok:
        return DecisionResult(False, "gate_fail", slack_ready, day_ok)
    if not data_ready:
        return DecisionResult(False, "data_not_ready", slack_ready, day_ok)
    if not day_ok:
        return DecisionResult(False, "one_per_day", slack_ready, day_ok)
    return DecisionResult(True, "OK", slack_ready, day_ok)


def evaluate_early(
    *,
    features: BarFeatures,
    base_params: HestonGateParams,
    config: EarlyConfig,
    context_priors: Optional[ContextPriors],
    bars_count: int,
    warmup_done: bool,
    data_ready: bool,
) -> EarlyDecision:
    if not config.enabled:
        return EarlyDecision(False, "disabled")
    if warmup_done:
        return EarlyDecision(False, "warmup_complete")
    if bars_count < config.min_bars:
        return EarlyDecision(False, "insufficient_bars")
    if config.require_calm:
        if not context_priors or context_priors.regime != "calm":
            return EarlyDecision(False, "context_not_calm")
    if not data_ready:
        return EarlyDecision(False, "data_not_ready")
    if config.require_rv30_le_rv60:
        if (
            (features.rv30 == features.rv30)
            and (features.rv60 == features.rv60)
            and (features.rv30 > features.rv60)
        ):
            return EarlyDecision(False, "rv30_spike")

    params = HestonGateParams(
        enabled=base_params.enabled,
        vrp_min=config.vrp_min,
        vrp_max=base_params.vrp_max,
        mr_max=config.mr_max,
        rv60_min=config.rv60_min,
        vov_max=config.vov_max,
    )

    gate = evaluate_heston_gate(
        enabled=params.enabled,
        iv_atm=features.iv_atm,
        rv30=features.rv30,
        rv60=features.rv60,
        rv90=features.rv90,
        vov=features.vov,
        params=params,
    )
    if not gate.ok:
        return EarlyDecision(False, gate.reason)

    return EarlyDecision(True, "OK")
