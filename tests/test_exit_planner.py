import json
from dataclasses import dataclass, replace
from datetime import datetime
from zoneinfo import ZoneInfo

from starship_engine.apps.captain_log.state import StateLogConfig, StateLogger
from starship_engine.apps.captain_log.state_log import StateLogHandler
from starship_engine.apps.exit_planner.eta import estimate_eta
from starship_engine.apps.exit_planner.planner import (
    build_exit_plan,
    build_exit_targets,
)
from starship_engine.apps.exit_planner.slack_fmt import format_exit_plan_slack
from starship_engine.apps.exit_planner.types import (
    ExitEta,
    ExitPlan,
    ExitPlannerConfig,
    ExitPosition,
)
from starship_engine.apps.heston.logic import HestonGateResult
from starship_engine.condor import Condor, OptionPoint
from starship_engine.domain.types import BarFeatures
from starship_engine.indicators import CandleBar


@dataclass
class FakeGreek:
    event_symbol: str
    delta: float
    gamma: float
    theta: float
    vega: float
    mark: float


def _cfg(**overrides) -> ExitPlannerConfig:
    base = ExitPlannerConfig(
        enabled=True,
        targets_enabled=True,
        eta_enabled=True,
        slack_enabled=False,
        slack_prefix="[EXIT]",
        tier_a_min_score=4,
        tier_b_min_score=2,
        tier_a_min=25,
        tier_a_max=35,
        tier_b_min=15,
        tier_b_max=25,
        tier_c_min=10,
        tier_c_max=15,
        eta_scenario_mode="fixed_points",
        eta_fixed_points=0.0,
        eta_expected_move_minutes=30,
        eta_max_minutes=180,
        eta_step_seconds=60,
        vov_window=3,
        vov_stable_threshold=0.002,
    )
    return replace(base, **overrides)


def _features(dt: datetime) -> BarFeatures:
    return BarFeatures(
        dt=dt,
        bar=CandleBar(open=100.0, high=101.0, low=99.5, close=100.5, time_ms=0),
        ema21=100.2,
        atr5=1.1,
        rv30=0.10,
        rv60=0.12,
        rv90=0.11,
        vov=0.0015,
        iv_atm=0.20,
        iv_disp=0.01,
        n_calls=5,
        n_puts=5,
        parse_fail=0,
        latest_spx_mid=5000.0,
        greeks_symbols=500,
        touch=True,
    )


def test_exit_targets_tier_a_midday():
    cfg = _cfg()
    dt = datetime(2026, 1, 23, 11, 0, tzinfo=ZoneInfo("America/New_York"))
    features = _features(dt)
    gate = HestonGateResult(ok=True, reason="OK", vrp=2.6, mr=-0.01, vov=0.0015)

    targets = build_exit_targets(
        features=features,
        gate=gate,
        vov_series=[0.0025, 0.0020, 0.0015],
        cfg=cfg,
    )

    assert targets.tier == "A"
    assert targets.primary_target_pct == 25
    assert targets.score >= 4


def test_eta_fixed_points_hits_targets():
    cfg = _cfg(eta_max_minutes=180)
    features = _features(
        datetime(2026, 1, 23, 11, 0, tzinfo=ZoneInfo("America/New_York"))
    )

    position = ExitPosition(
        credit_open=2.0,
        mark_now=2.0,
        theta_day=1.3,
        delta=0.0,
        gamma=0.0,
        vega=None,
        underlying_price=5000.0,
    )

    eta = estimate_eta(
        position=position,
        features=features,
        vrp=2.2,
        vov_series=[0.0020, 0.0018, 0.0016],
        cfg=cfg,
    )

    assert eta.enabled is True
    assert eta.to_15_pct["flat"] == [90, 90]
    assert eta.to_25_pct["flat"] == [150, 150]


def test_build_exit_plan_includes_primary_eta():
    cfg = _cfg()
    dt = datetime(2026, 1, 23, 11, 0, tzinfo=ZoneInfo("America/New_York"))
    features = _features(dt)
    gate = HestonGateResult(ok=True, reason="OK", vrp=2.6, mr=-0.01, vov=0.0015)

    condor = Condor(
        short_put=OptionPoint(event_symbol="SP", right="P", strike=4900, delta=-0.2),
        long_put=OptionPoint(event_symbol="LP", right="P", strike=4880, delta=-0.1),
        short_call=OptionPoint(event_symbol="SC", right="C", strike=5100, delta=0.2),
        long_call=OptionPoint(event_symbol="LC", right="C", strike=5120, delta=0.1),
    )

    greeks = {
        "SP": FakeGreek("SP", delta=-0.2, gamma=0.01, theta=-1.0, vega=0.1, mark=1.2),
        "LP": FakeGreek("LP", delta=-0.1, gamma=0.005, theta=-0.2, vega=0.05, mark=0.2),
        "SC": FakeGreek("SC", delta=0.2, gamma=0.01, theta=-1.0, vega=0.1, mark=1.2),
        "LC": FakeGreek("LC", delta=0.1, gamma=0.005, theta=-0.2, vega=0.05, mark=0.2),
    }

    plan = build_exit_plan(
        features=features,
        gate=gate,
        greeks_by_symbol=greeks,
        condor=condor,
        spot=features.latest_spx_mid,
        vov_series=[0.0025, 0.0020, 0.0015],
        cfg=cfg,
    )

    assert plan.tier == "A"
    assert plan.primary_target_pct == 25
    assert plan.eta.enabled is True
    assert plan.eta.to_primary_pct is not None
    assert set(plan.eta.to_primary_pct.keys()) == {"flat", "down", "up"}


def test_exit_plan_slack_format():
    plan = ExitPlan(
        tier="B",
        score=3,
        target_band_pct=(15, 25),
        primary_target_pct=15,
        reasons=["VRP high (2.1)", "VoV falling"],
        eta=ExitEta(
            enabled=True,
            confidence="medium",
            assumptions=None,
            to_15_pct={"flat": [20, 20], "down": [30, 30], "up": [40, 40]},
            to_25_pct={"flat": [45, 45], "down": [60, 60], "up": [80, 80]},
            to_primary_pct=None,
        ),
    )

    msg = format_exit_plan_slack(plan, "[EXIT]")
    assert "[EXIT] Exit Plan (Tier B | score 3/5)" in msg
    assert "Target: 15-25% (suggest 15%)" in msg
    assert "ETA: 15% ~ 20-40m | 25% ~ 45-80m" in msg
    assert "Confidence: Medium" in msg
    assert "Reasons: VRP high (2.1), VoV falling" in msg


def test_exit_plan_jsonl_logged(tmp_path):
    logger = StateLogger(
        StateLogConfig(enabled=True, out_dir=tmp_path, fmt="jsonl"),
        filename_base="spx0dte_state",
    )
    plugin = StateLogHandler(logger, every_bars=1)

    features = _features(
        datetime(2026, 1, 23, 11, 0, tzinfo=ZoneInfo("America/New_York"))
    )
    gate = HestonGateResult(ok=True, reason="OK", vrp=2.2, mr=-0.01, vov=0.0015)

    exit_plan = {
        "tier": "A",
        "score": 4,
        "target_band_pct": [25, 35],
        "primary_target_pct": 25,
        "reasons": ["VRP high (2.2)"],
        "eta": {"enabled": False, "reason": "disabled"},
    }

    plugin.maybe_write(
        bar_index=1,
        features=features,
        gate=gate,
        signal_mode="spx",
        context_priors=None,
        early_decision=None,
        slack_sent=False,
        exit_plan=exit_plan,
    )
    logger.close()

    path = next(tmp_path.glob("spx0dte_state_*.jsonl"))
    data = json.loads(path.read_text(encoding="utf-8").strip())
    assert "exit_plan" in data
    assert data["exit_plan"]["tier"] == "A"
    assert data["exit_plan"]["eta"]["enabled"] is False
