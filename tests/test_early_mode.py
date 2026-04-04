from datetime import datetime

from starship_engine.apps.context_overnight.logic import ContextPriors
from starship_engine.apps.heston.logic import HestonGateParams
from starship_engine.domain.decision import evaluate_early
from starship_engine.domain.types import BarFeatures, EarlyConfig
from starship_engine.indicators import CandleBar


def _features(rv30=0.10, rv60=0.12, rv90=0.11, iv_atm=0.18, vov=0.002):
    return BarFeatures(
        dt=datetime(2026, 1, 23, 10, 0),
        bar=CandleBar(open=100.0, high=101.0, low=99.0, close=100.5, time_ms=0),
        ema21=100.0,
        atr5=1.0,
        rv30=rv30,
        rv60=rv60,
        rv90=rv90,
        vov=vov,
        iv_atm=iv_atm,
        iv_disp=0.01,
        n_calls=5,
        n_puts=5,
        parse_fail=0,
        latest_spx_mid=100.5,
        greeks_symbols=500,
        touch=True,
    )


def test_early_disabled():
    cfg = EarlyConfig(
        enabled=False,
        min_bars=18,
        require_calm=True,
        require_rv30_le_rv60=True,
        vrp_min=1.6,
        mr_max=0.03,
        rv60_min=0.06,
        vov_max=None,
    )
    decision = evaluate_early(
        features=_features(),
        base_params=HestonGateParams(),
        config=cfg,
        context_priors=ContextPriors(0.1, 0.002, 0.004, "calm", 1.0),
        bars_count=20,
        warmup_done=False,
        data_ready=True,
    )
    assert decision.ok is False
    assert decision.reason == "disabled"


def test_early_requires_calm():
    cfg = EarlyConfig(
        enabled=True,
        min_bars=18,
        require_calm=True,
        require_rv30_le_rv60=True,
        vrp_min=1.6,
        mr_max=0.03,
        rv60_min=0.06,
        vov_max=None,
    )
    decision = evaluate_early(
        features=_features(),
        base_params=HestonGateParams(),
        config=cfg,
        context_priors=ContextPriors(0.1, 0.002, 0.004, "hot", -1.0),
        bars_count=20,
        warmup_done=False,
        data_ready=True,
    )
    assert decision.ok is False
    assert decision.reason == "context_not_calm"


def test_early_passes_when_strict_gate_ok():
    cfg = EarlyConfig(
        enabled=True,
        min_bars=18,
        require_calm=True,
        require_rv30_le_rv60=True,
        vrp_min=1.4,
        mr_max=0.05,
        rv60_min=0.06,
        vov_max=None,
    )
    decision = evaluate_early(
        features=_features(iv_atm=0.20, rv60=0.12, rv30=0.08, rv90=0.09),
        base_params=HestonGateParams(),
        config=cfg,
        context_priors=ContextPriors(0.1, 0.002, 0.004, "calm", 1.0),
        bars_count=20,
        warmup_done=False,
        data_ready=True,
    )
    assert decision.ok is True
    assert decision.reason == "OK"


def test_early_blocks_when_warmup_done():
    cfg = EarlyConfig(
        enabled=True,
        min_bars=18,
        require_calm=True,
        require_rv30_le_rv60=True,
        vrp_min=1.4,
        mr_max=0.05,
        rv60_min=0.06,
        vov_max=None,
    )
    decision = evaluate_early(
        features=_features(),
        base_params=HestonGateParams(),
        config=cfg,
        context_priors=ContextPriors(0.1, 0.002, 0.004, "calm", 1.0),
        bars_count=20,
        warmup_done=True,
        data_ready=True,
    )
    assert decision.ok is False
    assert decision.reason == "warmup_complete"
