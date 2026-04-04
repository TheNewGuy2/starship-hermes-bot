from starship_shared.enums import FactKind
from starship_shared.schemas import EngineFactV1, MarketRef


def _base_fact() -> dict:
    return {
        "schema_version": 1,
        "kind": FactKind.MARKET_FACT,
        "run_id": "2026-01-31",
        "seq": 1,
        "ts": "2026-01-31T17:44:20Z",
        "market": MarketRef(underlier="SPX", session="RTH", timeframe="5m"),
        "metrics": {"rv60": 0.12, "vrp": 1.35},
        "state": {"regime": "Short-Vol Friendly"},
        "events": [],
    }


def test_market_fact_schema_validates() -> None:
    fact = EngineFactV1(**_base_fact())
    assert fact.kind == FactKind.MARKET_FACT
    assert fact.market.underlier == "SPX"


def test_signal_fact_schema_validates() -> None:
    payload = _base_fact()
    payload["kind"] = FactKind.STRATEGY_SIGNAL
    payload["strategy"] = "condor"
    payload["signal"] = {
        "short_put": 6910,
        "long_put": 6890,
        "short_call": 6955,
        "long_call": 6975,
        "net_short_delta": 0.019,
        "width_put": 20,
        "width_call": 20,
        "slack_sent": True,
        "touch": True,
        "exit_plan": {
            "tier": "A",
            "score": 4,
            "target_band_pct": [25, 35],
            "primary_target_pct": 25,
        },
        "policy_summary": "TP: 45%/25%/25% | Stops: per-trade n/a, daily 1.5R | Max/day 3 (cutoff 2)",
        "policy_flat_by": "Flat by: 14:30 CST (no new entries after 13:45)",
    }

    fact = EngineFactV1(**payload)
    assert fact.kind == FactKind.STRATEGY_SIGNAL
    assert fact.signal is not None
    assert fact.signal["short_put"] == 6910


def test_engine_event_schema_validates() -> None:
    payload = _base_fact()
    payload["kind"] = FactKind.ENGINE_EVENT
    payload["events"] = [
        {
            "name": "startup",
            "payload": {"dry_run": False, "warmup_bars": 32},
        }
    ]

    fact = EngineFactV1(**payload)
    assert fact.kind == FactKind.ENGINE_EVENT
    assert fact.events
