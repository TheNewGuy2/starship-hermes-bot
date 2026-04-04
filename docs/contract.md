# Engine → Web Contract (Facts)

This doc defines the JSON payloads the engine publishes and the web ingests.  
All payloads are instances of `EngineFactV1`.

## Common envelope (all facts)

```json
{
  "schema_version": 1,
  "kind": "MARKET_FACT | STRATEGY_SIGNAL | ENGINE_EVENT",
  "run_id": "2026-01-31",
  "seq": 123,
  "ts": "2026-01-31T17:44:20Z",
  "market": {
    "underlier": "SPX",
    "session": "RTH",
    "timeframe": "5m"
  },
  "metrics": { "rv60": 0.12, "vrp": 1.35 },
  "state": { "regime": "Short-Vol Friendly" },
  "events": []
}
```

## MARKET_FACT

Snapshot of current metrics/state (per candle).

- `kind = "MARKET_FACT"`
- `metrics`: numeric values (close, ema21, rv60, vrp, etc.)
- `state`: booleans/strings describing readiness, regime, etc.

## STRATEGY_SIGNAL

Signal with legs, deltas, and optional exit plan.

```json
{
  "kind": "STRATEGY_SIGNAL",
  "strategy": "condor",
  "signal": {
    "short_put": 6910,
    "long_put": 6890,
    "short_call": 6955,
    "long_call": 6975,
    "net_short_delta": 0.019,
    "width_put": 20,
    "width_call": 20,
    "slack_sent": true,
    "touch": true,
    "exit_plan": {
      "tier": "A",
      "score": 4,
      "target_band_pct": [25, 35],
      "primary_target_pct": 25
    },
    "policy_summary": "TP: 45%/25%/25% | Stops: per-trade n/a, daily 1.5R | Max/day 3 (cutoff 2)",
    "policy_flat_by": "Flat by: 14:30 CST (no new entries after 13:45)"
  }
}
```

Notes:
- `metrics` includes numeric fields used by Slack formatting (close, ema21, iv_atm, rv60, vrp, mr, vov).
- `state` may include `gate_ok`, `gate_reason`, `signal_mode`, `ts_cst`.
- `slack_sent` now means the signal was emitted/published to web (Slack delivery happens in web).

Shadow diagnostics (optional, no behavior change):
- `max_abs_5m_return`: max absolute 5m return over last 60m window.
- `shadow_guard_active`: true when RV60 guard condition is active.
- `shadow_rv60_cap`: RV60 cap derived from max 5m return (annualized).
- `shadow_vrp`: VRP using the capped RV60.
- `shadow_gate_ok`: gate result using the shadow values.

## ENGINE_EVENT

Operational events or sentinel events.

```json
{
  "kind": "ENGINE_EVENT",
  "events": [
    {
      "name": "startup",
      "payload": { "dry_run": false, "warmup_bars": 32, "ignore_time_window": true }
    }
  ]
}
```

Sentinel events are sent as **raw domain events** (no Slack text). Web formats them.

```json
{
  "kind": "ENGINE_EVENT",
  "events": [
    {
      "type": "HEARTBEAT",
      "severity": "info",
      "snapshot": { "...": "..." },
      "state": { "...": "..." },
      "changes": []
    }
  ]
}
```

Common `ENGINE_EVENT` names:
- `startup`
- `warmup_complete`
- `stream_idle`
- `crash`
- `shutdown`

Sentinel event `type` values:
- `HEARTBEAT`
- `REGIME_CHANGE`
- `SCORE_CROSS`
- `VRP_BREAK`
- `VOV_SPIKE`
- `MR_BREAK`

When the event source is sentinel, `state.policy` may include the active `TradePolicy` for web formatting.

## Signing

Requests sent to `/engine/ingest` include:
- `X-Engine-Timestamp`
- `X-Engine-Signature`

Signature uses `sign_body_v1` from `starship_shared.signing`.
