# src/bot/exit_planner/types.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ExitPlannerConfig:
    enabled: bool
    targets_enabled: bool
    eta_enabled: bool
    slack_enabled: bool
    slack_prefix: str

    tier_a_min_score: int
    tier_b_min_score: int

    tier_a_min: int
    tier_a_max: int
    tier_b_min: int
    tier_b_max: int
    tier_c_min: int
    tier_c_max: int

    eta_scenario_mode: str
    eta_fixed_points: float
    eta_expected_move_minutes: int
    eta_max_minutes: int
    eta_step_seconds: int

    vov_window: int
    vov_stable_threshold: float


@dataclass(frozen=True)
class ExitTargets:
    tier: str
    score: int
    target_band_pct: tuple[int, int]
    primary_target_pct: int
    reasons: list[str]


@dataclass(frozen=True)
class ExitAssumptions:
    iv_const: bool
    price_scenarios: list[str]
    scenario_move: dict[str, object]


@dataclass(frozen=True)
class ExitEta:
    enabled: bool
    confidence: Optional[str] = None
    assumptions: Optional[ExitAssumptions] = None
    to_15_pct: Optional[dict[str, Optional[list[int]]]] = None
    to_25_pct: Optional[dict[str, Optional[list[int]]]] = None
    to_primary_pct: Optional[dict[str, Optional[list[int]]]] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class ExitPlan:
    tier: Optional[str]
    score: Optional[int]
    target_band_pct: Optional[tuple[int, int]]
    primary_target_pct: Optional[int]
    reasons: list[str]
    eta: ExitEta


@dataclass(frozen=True)
class ExitPosition:
    credit_open: float
    mark_now: float
    theta_day: float
    delta: float
    gamma: float
    vega: Optional[float]
    underlying_price: float
