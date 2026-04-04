import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from starship_reports.report import _summary, load_series


def test_report_summary_with_exit_plan(tmp_path: Path):
    path = tmp_path / "spx0dte_state_2026-01-27.jsonl"
    rows = [
        {
            "ts_cst": "2026-01-27 10:00:00",
            "close": 100.0,
            "ema21": 99.0,
            "iv_atm": 0.20,
            "rv30": 0.10,
            "rv60": 0.12,
            "rv90": 0.11,
            "vrp": 1.6,
            "mr": -0.01,
            "vov": 0.002,
            "gate_ok": True,
            "early_ok": None,
            "slack_sent": False,
            "greeks_symbols": 300,
            "exit_plan": {
                "tier": "A",
                "primary_target_pct": 25,
                "eta": {"enabled": True},
            },
        },
        {
            "ts_cst": "2026-01-27 10:05:00",
            "close": 101.0,
            "ema21": 99.5,
            "iv_atm": 0.19,
            "rv30": 0.09,
            "rv60": 0.11,
            "rv90": 0.10,
            "vrp": 1.7,
            "mr": -0.01,
            "vov": 0.002,
            "gate_ok": True,
            "early_ok": None,
            "slack_sent": False,
            "greeks_symbols": 300,
            "exit_plan": {
                "tier": "B",
                "primary_target_pct": 15,
                "eta": {"enabled": False},
            },
        },
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    series = load_series(path, ZoneInfo("America/Chicago"))
    summary = _summary(series)

    assert summary["bars"] == 2
    assert summary["exit_tier_a"] == 1
    assert summary["exit_tier_b"] == 1
    assert summary["exit_eta_enabled"] == 1
