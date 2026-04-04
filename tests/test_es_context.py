from starship_engine.apps.context_overnight.logic import (
    ContextPriors,
    compute_context_priors,
)
from starship_engine.apps.heston.logic import HestonGateParams
from starship_engine.domain.handlers import EsContextHandler
from starship_engine.indicators import CandleBar


def _make_bars(
    closes,
    *,
    highs=None,
    lows=None,
    start_ms=1_000_000,
    step_ms=300_000,
):
    bars = []
    highs = highs or closes
    lows = lows or closes
    for i, (c, h, l) in enumerate(zip(closes, highs, lows)):
        t = start_ms + i * step_ms
        bars.append(CandleBar(open=c, high=h, low=l, close=c, time_ms=t))
    return bars


def test_compute_context_priors_calm():
    closes = [100.0 + i * 0.01 for i in range(13)]
    highs = [c + 0.02 for c in closes]
    lows = [c - 0.02 for c in closes]
    priors = compute_context_priors(_make_bars(closes, highs=highs, lows=lows))
    assert priors.regime == "calm"
    assert priors.overnight_range_pct is not None


def test_compute_context_priors_hot_by_range():
    closes = [100.0 + i * 0.01 for i in range(13)]
    highs = [106.0 for _ in closes]
    lows = [94.0 for _ in closes]
    priors = compute_context_priors(_make_bars(closes, highs=highs, lows=lows))
    assert priors.regime == "hot"
    assert priors.overnight_range_pct is not None


def test_compute_context_priors_mixed():
    closes = [100.0 + i * 0.01 for i in range(13)]
    highs = [100.4 for _ in closes]
    lows = [99.6 for _ in closes]
    priors = compute_context_priors(_make_bars(closes, highs=highs, lows=lows))
    assert priors.regime == "mixed"


def test_context_adjustments_calm_and_hot():
    base = HestonGateParams(enabled=True, vrp_min=1.25, mr_max=0.05, rv60_min=0.05)
    plugin = EsContextHandler(enabled=True)

    plugin.latest_priors = ContextPriors(
        overnight_rv60=0.08,
        overnight_vov=0.002,
        overnight_range_pct=0.003,
        regime="calm",
        score=1.0,
    )
    calm_params = plugin.adjust_gate_params(base)
    assert calm_params.vrp_min == 1.15
    assert calm_params.mr_max == 0.07
    assert calm_params.rv60_min == 0.04

    plugin.latest_priors = ContextPriors(
        overnight_rv60=0.25,
        overnight_vov=0.01,
        overnight_range_pct=0.02,
        regime="hot",
        score=-1.0,
    )
    hot_params = plugin.adjust_gate_params(base)
    assert hot_params.vrp_min == 1.35
    assert round(hot_params.mr_max, 6) == 0.03
    assert round(hot_params.rv60_min, 6) == 0.06
