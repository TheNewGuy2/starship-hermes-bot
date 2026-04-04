# Starship Alpha Bot

SPX 0DTE iron condor signal bot with regime gating, Slack alerts, JSONL state logging, and an advisory Exit Planner. This is a signal and analytics system only; it does not auto-trade.

## Overview

The bot ingests SPX candles, quotes, and option Greeks, then applies a regime gate (VRP/MR/VoV + EMA touch) before emitting an iron condor signal. It logs every evaluation tick to JSONL for later analysis and can post concise alerts and status messages to Slack. Optional ES context priors can adjust thresholds based on overnight /ES behavior.

## Features (Detailed)

### 1) Regime Gating (Heston-style)

The Heston gate determines whether the market is stable enough to sell premium. It uses the following metrics:

- **IV_ATM / IV0DTE**: At-the-money implied volatility for 0DTE options.
- **RV30 / RV60 / RV90**: Realized volatility from recent 5m bars.
- **VRP (Vol Risk Premium)**: `IV_ATM / RV60`. Higher is better for selling premium.
- **MR (Mean Reversion Proxy)**: `RV30 - RV90`. Lower is better (less front-loaded volatility).
- **VoV (Vol of Vol)**: Volatility of volatility from return dispersion.
- **EMA21 touch**: Mean-reversion trigger (price close near EMA21 within ATR tolerance).

The gate is considered OK only when VRP is high enough, MR is below the ceiling, RV60 is not too low, and VoV is not excessive (if enabled). EMA touch is required by default for entries.

### 2) Signal Construction (Iron Condor)

On a valid gate + touch, the bot builds a 0DTE SPXW iron condor by selecting ~20Δ shorts and $20 wide wings. It optionally nudges for delta neutrality. This is advisory-only: no orders are placed.

### 3) Slack Integration

Slack messaging is optional and rate-limited. Message types:

- **Signal Alerts**: Fired when a valid trade setup is detected.
- **Regime Change**: Optional status updates (SKIP/OK/CAUTION) with key metrics.
- **Heartbeat**: Periodic status messages so you can confirm the bot is alive.
- **Stream Health**: Idle stream warnings when data pauses.

Slack output is intentionally compact and can be throttled to avoid noise.

**Slack Risk Sentinel (Mode A)** adds event-driven risk alerts (regime changes, score crosses, VRP/VoV/MR breaks) plus a 15-minute heartbeat during RTH. It is webhook-only and designed to reuse the same event model for future sinks. It uses per-event cooldowns and still respects the global Slack cooldown.

### 4) Exit Planner (Advisory)

The Exit Planner provides guidance only (no auto-exit):

- **Target bands**: Chooses a profit target band based on regime quality (Tier A/B/C).
- **ETA (optional)**: Uses portfolio Greeks to estimate time-to-target under simple scenarios.

**Target scoring** (0–5):
- +1 if VRP >= 1.8
- +1 if VRP >= 2.5
- +1 if VoV is falling or stable
- +1 if RV30 < RV60 (compression)
- +1 if time-of-day is in the decay window

**Tier mapping**:
- Tier A: score >= 4 → 25–35%
- Tier B: score >= 2 → 15–25%
- Tier C: otherwise → 10–15%

**ETA assumptions**:
- Constant IV
- Flat/down/up price scenarios
- Expected move or fixed points

Exit Planner output is stored in JSONL and can optionally append to Slack alerts.

### 5) Trend Risk (Secondary Gate)

Trend Risk is a secondary ADX/DI + stretch risk score (0–10) to detect directional drift on low‑vol days that can still hurt iron condors. It is off by default and configurable in `configs/bot.yaml` under `trend_risk`. The score is logged per bar in JSONL under a `trend` object.

**Score components**:
- ADX level (0–4)
- ADX slope/acceleration (0–2)
- DI dominance / spread (0–2)
- Price stretch vs EMA21 in ATR units (0–2)
- Optional RV30 vs RV60 modifier (−1 / 0 / +1)

**Policy options**:
- `veto_or_tier`: block on `block` tier; allow `caution` only if base exit score is Tier A
- `tier_only`: never block; cap exit targets to Tier B on caution/block
- `warn_only`: annotate only

### 6) ES Context Mode (Optional)
### 5) ES Context Mode (Optional)

This adds /ES overnight priors to softly adjust SPX thresholds. It is off by default.

```yaml
# configs/bot.yaml
runner:
  # Default (SPX-only behavior)
  signal_mode: spx
  exec_symbol: SPX

  # Experimental: ES context priors adjust thresholds
  # signal_mode: es_context
  # context_symbol: /ES
```

Optional: resolve the ES streamer symbol via the tastytrade API (uses `/instruments/future-products`).

```yaml
# configs/bot.yaml
runner:
  # Auto-resolve /ES to /ES:XCME using the futures product endpoint
  context_symbol: ES
  context_exchange: CME
  context_code: ES
```

Overnight window used for ES priors:
- Prior day 16:00 ET → today 09:30 ET

Priors computed:
- `overnight_rv60` (last 60m of overnight)
- `overnight_vov` (VoV on overnight returns)
- `overnight_range_pct` (overnight high/low range as %)
- `regime = calm | mixed | hot`

Regime effects (small, conservative):
- calm → slightly lower `vrp_min`, slightly higher `mr_max`, slightly lower `rv60_min`
- hot → slightly higher `vrp_min`, slightly lower `mr_max`, slightly higher `rv60_min`

### 7) Early Mode (Shadow)

Early mode evaluates a stricter gate before warmup completes. It never triggers alerts unless you wire it to do so. It is useful for diagnostics and research.

### 8) JSONL State Logging + Reports

Every evaluation tick can be logged to JSONL with all features, gate results, and (if enabled) the Exit Planner block. A daily report generator turns JSONL into a chart and a short summary.

## Metrics Guide (Definitions + Rules of Thumb)

These are starting points aligned with current gate logic and common 0DTE practice.

- **IV_ATM / IV0DTE**: ATM implied volatility for 0DTE options. Higher = more priced risk.
  Rule of thumb: compare to RV60; IV should be meaningfully above RV60 to justify selling premium.
- **RV30 / RV60 / RV90**: Realized vol over the last ~30/60/90 minutes (5m bars).
  Rule of thumb: RV60 should be at least ~5% annualized to avoid tiny-RV distortion.
- **VRP (vol risk premium)**: `IV_ATM / RV60`.
  Rule of thumb: VRP >= 1.25 is “good”; below that is “bad”.
- **MR (mean reversion proxy)**: `RV30 - RV90`.
  Rule of thumb: MR <= 0.05 is “good”; above that is “bad” (rising front vol).
- **VoV (vol of vol)**: std dev of absolute returns (from RV60 returns).
  Rule of thumb: keep under a session-calibrated cap; start around 0.006–0.010 and tune.
- **disp (IV dispersion)**: p90 - p10 of IV around ATM.
  Rule of thumb: lower is “good”; spikes can indicate unstable pricing.
- **EMA21 touch**: close is near EMA21 using ATR-based tolerance.
  Rule of thumb: required for entries by default; set `runner.require_ema_touch: false` in `configs/bot.yaml` to bypass.

## Trend Risk Env Vars

```bash
TREND_RISK_ENABLED=0
TREND_RISK_ADX_LEN=14
TREND_RISK_DI_LEN=14
TREND_RISK_SLOPE_LOOKBACK=3
TREND_RISK_USE_RV_MODIFIER=1
TREND_RISK_BLOCK=8
TREND_RISK_CAUTION=6
TREND_RISK_STRETCH_ATR_LEN=5
TREND_RISK_STRETCH_LOW=0.7
TREND_RISK_STRETCH_HIGH=1.3
TREND_RISK_DI_SPREAD_LOW=6
TREND_RISK_DI_SPREAD_HIGH=12
TREND_RISK_ADX_SLOPE_LOW=0.5
TREND_RISK_ADX_SLOPE_HIGH=1.5
TREND_RISK_ADX_B1=20
TREND_RISK_ADX_B2=25
TREND_RISK_ADX_B3=30
TREND_RISK_ADX_B4=35
TREND_RISK_POLICY=veto_or_tier
```

## Scoring Summary

There are two independent scores today:

- **Exit Planner score (0–5)**: drives Tier A/B/C exit targets (advisory only).
- **Trend Risk score (0–10)**: detects directional drift risk; can veto, warn, or cap targets depending on policy.

In `veto_or_tier`, a Trend Risk **caution** allows signals only when the Exit Planner score qualifies as Tier A. This does **not** require the Exit Planner feature to be enabled; the score is computed from the same features regardless.

## Quickstart

1) Create `.env` from `.env.example` and fill in your tastytrade credentials.
2) Install deps:

```bash
uv sync
```

3) Run the bot:

```bash
uv run bot
```

Optional: use the CLI to override `configs/bot.yaml`:

```bash
uv run cli run --signal-mode es_context --context-symbol ES
```

## Configuration

Configuration lives in `configs/bot.yaml` (copy `configs/bot.yaml.example`). Only secrets live in `.env`.

Sections you will edit most often:
- `runner`: runtime settings (signal mode, warmup, early mode, logging, trade policy defaults)
- `sentinel`: thresholds and heartbeat
- `trend_risk`: trend risk gate
- `exit_planner`: exit planner settings
- `heston`: gate thresholds
- `comms`: ingest URL/secret + JSONL path
- `auth`: Tastytrade credentials (overridden by `.env`)

Minimal `.env`:

```bash
TT_CLIENT_ID=...
TT_CLIENT_SECRET=...
TT_REFRESH_TOKEN=...
TT_IS_TEST=false
SLACK_WEBHOOK_URL=...
```

## CLI

The CLI mirrors a subset of runner settings:

```bash
uv run cli run --help
```

Use CLI flags to override `configs/bot.yaml` for a session (secrets stay in `.env`).

## Pipeline

Core behavior is modularized in `packages/starship_engine/src/starship_engine/pipeline` as handlers:

- `HestonGateHandler`: regime gating (VRP/MR/VoV)
- `SlackAlertHandler`: Slack notifications
- `StateLogHandler`: JSONL/CSV state logs
- `StreamHealthHandler`: stream liveness warnings
- `EsContextHandler`: optional ES priors
- `ExitPlannerHandler`: advisory exit plans

## Examples

Minimal `.env` for local run:

```bash
TT_CLIENT_ID=...
TT_CLIENT_SECRET=...
TT_REFRESH_TOKEN=...
SLACK_WEBHOOK_URL=...
```

Example Exit Planner JSONL block:

```json
"exit_plan": {
  "tier": "A",
  "score": 4,
  "target_band_pct": [25, 35],
  "primary_target_pct": 25,
  "reasons": ["VRP high (2.7)", "VoV falling"],
  "eta": {"enabled": false, "reason": "disabled"}
}
```

Example Slack Exit Plan block:

```
[EXIT] Exit Plan (Tier A | score 4/5)
Target: 25-35% (suggest 25%)
ETA: 15% ~ 20-35m | 25% ~ 40-75m
Confidence: Medium
Reasons: VRP high (2.7), VoV falling
```

## Reports

Generate a daily chart + summary from JSONL state logs:

```bash
uv run starship-report --date 2026-01-23
```

Or via Make:

```bash
make report DATE=2026-01-23
```

Outputs:
- `reports/2026-01-23_report.png`
- `reports/2026-01-23_summary.txt`

## Tests

```bash
uv run pytest
```

## Alpha Sim Lab (Research)

Alpha Sim Lab is a **research-only handler** used to run fast hypothesis tests with free data. It is intentionally separate from live trading.
Default data source is Yahoo via `yfinance` (symbols: `^GSPC`, `^VIX`). Stooq is also supported; use `^SPX` for SPX on Stooq.  
For VIX on Stooq, use `vi.f` (per your link). If Stooq VIX fails, run with `--vix-source yahoo` to fetch VIX from Yahoo while keeping SPX from Stooq.

### How the IC Filter Backtest Works

The IC filter uses **daily** SPX bars and VIX as an IV proxy, then simulates “trade vs no-trade” using the same Heston-style gate as the live starship_engine.

Trade decision (gate):
- `iv_atm` = VIX / 100
- `rv30`, `rv60`, `rv90` from rolling realized vol on daily closes
- `mr` = RV30 − RV90 (mean-reversion proxy)
- `vrp` = IV / RV60 (vol risk premium)
- `vov` from daily return dispersion (optional)
- **Trade if** all thresholds pass (VRP ≥ min, RV60 ≥ min, MR ≤ max, optional VoV cap).

Outcome definition (IC proxy):
- `range_pct = (high - low) / open`
- If `range_pct <= ic_range`, that day is counted as a “good IC day”.

The backtest then computes a confusion matrix (TP/FP/TN/FN) and summary metrics based on those definitions.

Run a YAML-defined suite (preferred):

```bash
uv run alpha-sim configs/suites/ic_filter_phase25.yaml
```

Promote a winning run to an active TradePolicy:

```bash
uv run cli policy promote runs/.../summary.json --name ic_harvest_v1 --set-active
```

Outputs go to:
`sim_lab/runs/ic_filter/<timestamp>/`

Files:
- `summary.json`
- `daily.csv`
- `daily.parquet` (if `pyarrow` is installed)
- `failures.csv` (FP days)

## Future Plans

- Early signals informed by ES context priors (beyond shadow mode)
- Exit Planner ETA with IV/vega scenarios (not just constant IV)
- Position-aware max-hold guidance for intraday management
