# Reporting

Reports are generated from daily JSONL state logs (`data/spx0dte_state_YYYY-MM-DD.jsonl`).
These files are written by the engine’s StateLogger.

## Run

```bash
uv run starship-report --date 2026-02-02
```

Or point at a specific file:

```bash
uv run starship-report --input data/spx0dte_state_2026-02-02.jsonl --out-dir reports/2026-02-02
```

## Outputs

The report writes:
- `<out-dir>/<date>_report.png`
- `<out-dir>/<date>_summary.txt`

Default `out-dir` is `reports/`.

## Shadow RV60 guard (diagnostic only)

Reports include a shadow guard that **does not change live behavior**. It flags
when RV60 looks high despite very small 5‑minute moves, and shows how many
additional bars would have passed the gate under the shadow rule.

Fields:
- `max_abs_5m_return`
- `shadow_guard_active`
- `shadow_rv60_cap`
- `shadow_vrp`
- `shadow_gate_ok`
