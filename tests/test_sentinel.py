from dataclasses import asdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from starship_engine.apps.sentinel.core import SentinelParams, SlackSentinel
from starship_engine.apps.sentinel.state import StateParams
from starship_shared.sentinel import (
    MarketSnapshot,
    MarketState,
    SentinelEvent,
    SentinelEventType,
)

from starship_web.sentinel_format import format_sentinel_event


def _snapshot(ts: datetime, vrp: float, vov: float, mr: float) -> MarketSnapshot:
    return MarketSnapshot(
        ts=ts,
        symbol="SPX",
        timeframe="5m",
        iv_atm=0.20,
        rv30=0.18,
        rv60=0.16,
        rv90=0.15,
        vrp=vrp,
        vov=vov,
        mr=mr,
        adx=None,
        extra={},
    )


def _sentinel(**overrides) -> SlackSentinel:
    params = SentinelParams(
        score_thresholds=[70, 50, 30],
        hysteresis=5,
        vrp_min=1.25,
        mr_max=0.05,
        vov_caution=0.25,
        vov_hostile=0.35,
        vov_hysteresis=0.02,
        cooldown_regime_change_seconds=600,
        cooldown_score_cross_seconds=600,
        cooldown_vrp_seconds=600,
        cooldown_vov_seconds=600,
        cooldown_mr_seconds=600,
    )
    state_params = StateParams(
        vrp_friendly_min=1.25,
        vrp_caution_min=1.10,
        mr_max=0.05,
        vov_caution=0.25,
        vov_hostile=0.35,
    )
    tz = ZoneInfo("America/Chicago")
    base = dict(
        params=params,
        state_params=state_params,
        tz=tz,
        enabled=True,
        rth_only=False,
        heartbeat_enabled=False,
        heartbeat_minutes=15,
        heartbeat_rth_only=False,
    )
    base.update(overrides)
    return SlackSentinel(**base)


def test_score_cross_hysteresis():
    sentinel = _sentinel()
    ts = datetime(2026, 1, 23, 10, 0, tzinfo=ZoneInfo("America/Chicago"))

    high = _snapshot(ts, vrp=1.30, vov=0.10, mr=0.01)
    low = _snapshot(ts + timedelta(minutes=5), vrp=1.20, vov=0.10, mr=0.06)
    back_high = _snapshot(ts + timedelta(minutes=10), vrp=2.00, vov=0.05, mr=0.00)
    low_later = _snapshot(ts + timedelta(minutes=20), vrp=1.20, vov=0.10, mr=0.06)

    events = sentinel.on_candle_close(high)
    assert not any(e.type == SentinelEventType.SCORE_CROSS for e in events)

    events = sentinel.on_candle_close(low)
    assert any(e.type == SentinelEventType.SCORE_CROSS for e in events)

    events = sentinel.on_candle_close(low)
    assert not any(e.type == SentinelEventType.SCORE_CROSS for e in events)

    sentinel.on_candle_close(back_high)
    events = sentinel.on_candle_close(low_later)
    assert any(e.type == SentinelEventType.SCORE_CROSS for e in events)


def test_regime_change_triggers():
    sentinel = _sentinel()
    ts = datetime(2026, 1, 23, 10, 0, tzinfo=ZoneInfo("America/Chicago"))

    friendly = _snapshot(ts, vrp=1.35, vov=0.10, mr=0.01)
    hostile = _snapshot(ts + timedelta(minutes=5), vrp=1.00, vov=0.40, mr=0.06)

    sentinel.on_candle_close(friendly)
    events = sentinel.on_candle_close(hostile)
    regime = [e for e in events if e.type == SentinelEventType.REGIME_CHANGE]
    assert regime
    assert regime[0].severity == "critical"


def test_heartbeat_window():
    sentinel = _sentinel(heartbeat_enabled=True)
    ts = datetime(2026, 1, 23, 10, 2, tzinfo=ZoneInfo("America/Chicago"))
    snap = _snapshot(ts, vrp=1.30, vov=0.10, mr=0.01)

    events = sentinel.on_candle_close(snap)
    assert any(e.type == SentinelEventType.HEARTBEAT for e in events)

    events = sentinel.on_candle_close(
        _snapshot(ts + timedelta(minutes=8), 1.30, 0.10, 0.01)
    )
    assert not any(e.type == SentinelEventType.HEARTBEAT for e in events)

    events = sentinel.on_candle_close(
        _snapshot(ts + timedelta(minutes=16), 1.30, 0.10, 0.01)
    )
    assert any(e.type == SentinelEventType.HEARTBEAT for e in events)


def test_cooldown_suppresses_repeat():
    sentinel = _sentinel()
    ts = datetime(2026, 1, 23, 10, 0, tzinfo=ZoneInfo("America/Chicago"))
    snap = _snapshot(ts, vrp=1.0, vov=0.10, mr=0.01)
    state = MarketState(regime_label="Caution", score=60, flags=[], reasons=[])

    event = SentinelEvent(
        type=SentinelEventType.VRP_BREAK,
        ts=ts,
        snapshot=snap,
        state=state,
        changes=["VRP < 1.25"],
        severity="warn",
    )

    sent_first = sentinel._apply_cooldown([event], ts)
    sent_second = sentinel._apply_cooldown([event], ts + timedelta(seconds=100))

    assert len(sent_first) == 1
    assert len(sent_second) == 0


def test_format_includes_data_ready_flag():
    ts = datetime(2026, 1, 23, 9, 0, tzinfo=ZoneInfo("America/Chicago"))
    snap = _snapshot(ts, vrp=1.1, vov=0.30, mr=0.06)
    snap.extra["data_ready"] = False
    state = MarketState(regime_label="Caution", score=50, flags=["VRP?"], reasons=[])
    event = SentinelEvent(
        type=SentinelEventType.VRP_BREAK,
        ts=ts,
        snapshot=snap,
        state=state,
        changes=["VRP < 1.25"],
        severity="warn",
    )
    msg = format_sentinel_event(asdict(event), policy=None)
    assert "Data: not ready" in msg
