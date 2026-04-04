from datetime import datetime

from starship_engine.apps.heston.logic import HestonGateResult
from starship_engine.domain.decision import decide
from starship_engine.domain.types import BarFeatures
from starship_engine.indicators import CandleBar


def _features(touch=True):
    return BarFeatures(
        dt=datetime(2026, 1, 23, 10, 0),
        bar=CandleBar(open=100.0, high=101.0, low=99.0, close=100.5, time_ms=0),
        ema21=100.0,
        atr5=1.0,
        rv30=0.10,
        rv60=0.12,
        rv90=0.11,
        vov=0.002,
        iv_atm=0.15,
        iv_disp=0.01,
        n_calls=5,
        n_puts=5,
        parse_fail=0,
        latest_spx_mid=100.5,
        greeks_symbols=500,
        touch=touch,
    )


def test_decision_blocks_touch():
    gate = HestonGateResult(ok=True, reason="OK", vrp=1.3, mr=-0.01, vov=0.002)
    res = decide(
        features=_features(touch=False),
        gate=gate,
        data_ready=True,
        slack_ready=True,
        day_ok=True,
        dry_run=False,
    )
    assert res.ok is False
    assert res.reason == "no_touch"


def test_decision_all_ok():
    gate = HestonGateResult(ok=True, reason="OK", vrp=1.3, mr=-0.01, vov=0.002)
    res = decide(
        features=_features(touch=True),
        gate=gate,
        data_ready=True,
        slack_ready=True,
        day_ok=True,
        dry_run=False,
    )
    assert res.ok is True
    assert res.reason == "OK"
