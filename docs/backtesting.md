# Backtesting (SimLab)

Preferred: **run via suite config**. It is repeatable and easy to share.

## Run a suite config (preferred)

```bash
uv run alpha-sim configs/suites/ic_filter_phase25.yaml
```

Common options:
- `--dry-run` validate only
- `--only <test_name>` run a single test
- `--outdir <path>` override output base
- `--fail-fast` stop on first error

## Suite config format

Example file: `configs/suites/ic_filter_phase25.yaml`

```yaml
schema_version: 1
suite_name: "ic_filter_phase25"

defaults:
  plugin: "alpha_sim_lab.ic_filter"
  mode: "pnl"
  phase: 2.5

  data_source: "yahoo"
  symbol: "^GSPC"
  vix_symbol: "^VIX"
  start: "2016-01-01"
  end: "2026-01-29"

  vrp_min: 1.25
  rv60_min: 0.05
  mr_max: 0.05

  loss_model: "scaled"
  loss_floor_r: 1.0
  max_loss_r: 2.0
  stop_r: 1.5
  warmup_entry_mode: "slot_proxy"
  warmup_skip_first_trade: true
  earliest_entry_time_cst: "10:50"

tests:
  - name: "seq_tp_cutoff3_stop1p5"
    params:
      ic_range: 0.012
      max_trades_per_day: 3
      entry_cutoff_trades: 3
      daily_stop_r: 1.5
      tp_r_seq: [0.45, 0.25, 0.25]
```

### Top-level fields
- `schema_version`: config schema version (currently `1`).
- `suite_name`: label used to name the output folder.
- `defaults`: base params applied to every test.
- `tests`: list of test cases, each with `name` + `params` and optional `sweep`.

### `defaults` + `params` fields
Core:
- `plugin`: SimLab handler. Keep `alpha_sim_lab.ic_filter` for IC filter runs.
- `mode`: `classify` or `pnl`.
- `phase`: `2.0` or `2.5`.

Data:
- `data_source`: `yahoo` or `stooq`.
- `symbol`: underlying symbol (`^GSPC` for Yahoo, `^SPX` for Stooq).
- `vix_symbol`: IV proxy symbol (`^VIX` for Yahoo, `vi.f` for Stooq).
- `start` / `end`: date strings `YYYY-MM-DD` (inclusive).
- `input_csv`: optional local SPX CSV path (bypasses `data_source`).
- `vix_csv`: optional local VIX CSV path (bypasses `data_source`).
- `vix_source`: optional override to fetch VIX from a different source than SPX.

Gate thresholds:
- `ic_range`: IC day threshold as range percent (e.g., `0.012`).
- `vrp_min`: minimum VRP (IV / RV60).
- `vrp_max`: optional maximum VRP cap.
- `rv60_min`: minimum 60‑day realized vol.
- `mr_max`: max mean‑reversion proxy (RV30 − RV90).
- `vov_max`: optional VoV cap.

PnL + trade logic (mode=`pnl`):
- `tp_r`: take‑profit in R for single TP mode.
- `tp_r_seq`: per‑trade TP sequence (e.g., `[0.45, 0.25, 0.25]`).
- `tp_r_grid`: comma list or used in `sweep` for TP grid searches.
- `max_loss_r`: maximum loss in R.
- `stop_r`: optional per‑trade stop in R.
- `loss_model`: `scaled` or `binary`.
- `loss_floor_r`: floor for scaled loss model.
- `max_trades_per_day`: max trades/day.
- `entry_cutoff_trades`: late‑day cutoff proxy (number of entry slots).
- `daily_stop_r`: daily stop in R.
- `late_trade_tp_mult`: TP multiplier for trades #2+ when `tp_r_seq` not set.
- `warmup_entry_mode`: keep `slot_proxy`.
- `warmup_skip_first_trade`: skip the first slot as a warm‑up proxy.
- `earliest_entry_time_cst`: earliest entry time.

### `sweep` fields
Use `sweep` to grid search a single parameter:

```yaml
tests:
  - name: "sweep_tp_cutoff2_stop1p5"
    params:
      ic_range: 0.012
      max_trades_per_day: 3
      entry_cutoff_trades: 2
      daily_stop_r: 1.5
    sweep:
      tp_r: [0.25, 0.35, 0.45, 0.50]
```

## Outputs

Suite outputs: `sim_lab/runs/suites/<suite>/<timestamp>/`

When `mode=pnl` and charts are enabled (default), PNGs are written next to `summary.json`:
`equity.png`, `drawdown.png`, `daily_pnl.png`, `trades.png`.
