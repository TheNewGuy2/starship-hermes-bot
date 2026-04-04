from __future__ import annotations

from starship_engine.apps.captain_log.logging import get_logger
from starship_engine.apps.exit_planner.settings import ExitPlannerSettings
from starship_engine.apps.exit_planner.types import ExitPlannerConfig


class ExitPlannerApp:
    name = "exit_planner"
    settings = ExitPlannerSettings()
    logger = get_logger(__name__)


app = ExitPlannerApp()
name = app.name


def to_config(settings: ExitPlannerSettings) -> ExitPlannerConfig:
    return ExitPlannerConfig(
        enabled=settings.enabled,
        targets_enabled=settings.targets_enabled,
        eta_enabled=settings.eta_enabled,
        slack_enabled=settings.slack_enabled,
        slack_prefix=settings.slack_prefix,
        tier_a_min_score=settings.tier_a_min_score,
        tier_b_min_score=settings.tier_b_min_score,
        tier_a_min=settings.tier_a_min,
        tier_a_max=settings.tier_a_max,
        tier_b_min=settings.tier_b_min,
        tier_b_max=settings.tier_b_max,
        tier_c_min=settings.tier_c_min,
        tier_c_max=settings.tier_c_max,
        eta_scenario_mode=settings.eta_scenario_mode,
        eta_fixed_points=settings.eta_fixed_points,
        eta_expected_move_minutes=settings.eta_expected_move_minutes,
        eta_max_minutes=settings.eta_max_minutes,
        eta_step_seconds=settings.eta_step_seconds,
        vov_window=settings.vov_window,
        vov_stable_threshold=settings.vov_stable_threshold,
    )
