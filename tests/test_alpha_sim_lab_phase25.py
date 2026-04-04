# tests/test_alpha_sim_lab_phase25.py
from __future__ import annotations

import csv
from datetime import date

from starship_simlab.data_sources import DailyBar
from starship_simlab.ic_filter import IcFilterParams, run_ic_filter


def _read_csv(path):
    with open(path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_phase25_sequential_cutoff_and_late_tp(tmp_path, monkeypatch):
    spx = [
        DailyBar(date=date(2025, 1, 2), open=100.0, high=101.0, low=99.5, close=100.5),
        DailyBar(date=date(2025, 1, 3), open=100.0, high=103.0, low=97.0, close=99.0),
    ]
    vix = [
        DailyBar(date=date(2025, 1, 2), open=0.0, high=0.0, low=0.0, close=20.0),
        DailyBar(date=date(2025, 1, 3), open=0.0, high=0.0, low=0.0, close=20.0),
    ]

    def _load_data(_params):
        return spx, vix

    def _rolling_rv(_closes, _window):
        return [0.10, 0.10]

    monkeypatch.setattr("starship_simlab.ic_filter._load_data", _load_data)
    monkeypatch.setattr("starship_simlab.ic_filter._rolling_rv", _rolling_rv)

    params = IcFilterParams(
        start=date(2025, 1, 2),
        end=date(2025, 1, 3),
        ic_range=0.02,
        vrp_min=1.25,
        vrp_max=None,
        rv60_min=0.05,
        mr_max=0.05,
        vov_max=None,
        symbol="^GSPC",
        vix_symbol="^VIX",
        data_source="yahoo",
        input_csv=None,
        vix_csv=None,
        mode="pnl",
        output_format="csv",
        phase=2.5,
        tp_r=0.4,
        max_loss_r=4.0,
        stop_r=None,
        loss_model="scaled",
        loss_floor_r=1.0,
        max_trades_per_day=3,
        entry_cutoff_trades=2,
        late_trade_tp_mult=0.5,
        daily_stop_r=None,
        warmup_skip_first_trade=False,
    )

    run_ic_filter(params, out_dir=str(tmp_path))

    rows = _read_csv(tmp_path / "daily.csv")
    by_date = {row["date"]: row for row in rows}

    day1 = by_date["2025-01-02"]
    day2 = by_date["2025-01-03"]

    assert int(day1["trades_taken"]) == 2
    assert abs(float(day1["day_pnl_r"]) - 0.6) < 1e-6
    assert int(day1["effective_max_trades"]) == 2

    assert int(day2["trades_taken"]) == 1
    assert abs(float(day2["day_pnl_r"]) + 4.0) < 1e-6
