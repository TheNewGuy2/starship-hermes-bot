from __future__ import annotations

from pydantic import BaseModel


class ExitPlannerSettings(BaseModel):
    enabled: bool = False
    targets_enabled: bool = True
    eta_enabled: bool = False
    slack_enabled: bool = False
    slack_prefix: str = "[EXIT]"
    tier_a_min_score: int = 4
    tier_b_min_score: int = 2
    tier_a_min: int = 25
    tier_a_max: int = 35
    tier_b_min: int = 15
    tier_b_max: int = 25
    tier_c_min: int = 10
    tier_c_max: int = 15
    eta_scenario_mode: str = "expected_move"
    eta_fixed_points: float = 10.0
    eta_expected_move_minutes: int = 30
    eta_max_minutes: int = 180
    eta_step_seconds: int = 60
    vov_window: int = 3
    vov_stable_threshold: float = 0.002
