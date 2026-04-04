# tests/test_alpha_sim_lab_warmup_tp_seq.py
from __future__ import annotations

import json
from datetime import date

from starship_simlab.data_sources import DailyBar
from starship_simlab.ic_filter import IcFilterParams, run_ic_filter


def _write_summary(tmp_path, params, monkeypatch):
    spx = [
        DailyBar(date=date(2025, 1, 2), open=100.0, high=101.0, low=99.5, close=100.5),
    ]
    vix = [
        DailyBar(date=date(2025, 1, 2), open=0.0, high=0.0, low=0.0, close=20.0),
    ]

    def _load_data(_params):
        return spx, vix

    def _rolling_rv(_closes, _window):
        return [0.10]

    monkeypatch.setattr("starship_simlab.ic_filter._load_data", _load_data)
    monkeypatch.setattr("starship_simlab.ic_filter._rolling_rv", _rolling_rv)

    run_ic_filter(params, out_dir=str(tmp_path))
    return json.loads((tmp_path / "summary.json").read_text())


def test_warmup_keeps_tp_sequence(monkeypatch, tmp_path):
    params = IcFilterParams(
        start=date(2025, 1, 2),
        end=date(2025, 1, 2),
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
        tp_r=0.35,
        max_loss_r=4.0,
        stop_r=None,
        loss_model="scaled",
        loss_floor_r=1.0,
        max_trades_per_day=3,
        entry_cutoff_trades=3,
        late_trade_tp_mult=None,
        daily_stop_r=None,
        tp_r_seq=[0.14, 0.10, 0.10],
        warmup_skip_first_trade=True,
    )

    summary = _write_summary(tmp_path, params, monkeypatch)
    sim_params = summary["params"]

    assert sim_params["effective_max_trades_per_day"] == 2
    assert sim_params["tp_targets_used"] == [0.14, 0.10]


def test_no_warmup_uses_full_sequence(monkeypatch, tmp_path):
    params = IcFilterParams(
        start=date(2025, 1, 2),
        end=date(2025, 1, 2),
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
        tp_r=0.35,
        max_loss_r=4.0,
        stop_r=None,
        loss_model="scaled",
        loss_floor_r=1.0,
        max_trades_per_day=3,
        entry_cutoff_trades=3,
        late_trade_tp_mult=None,
        daily_stop_r=None,
        tp_r_seq=[0.14, 0.10, 0.10],
        warmup_skip_first_trade=False,
    )

    summary = _write_summary(tmp_path, params, monkeypatch)
    sim_params = summary["params"]

    assert sim_params["effective_max_trades_per_day"] == 3
    assert sim_params["tp_targets_used"] == [0.14, 0.10, 0.10]


def test_tp_r_fallback_with_warmup(monkeypatch, tmp_path):
    params = IcFilterParams(
        start=date(2025, 1, 2),
        end=date(2025, 1, 2),
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
        tp_r=0.35,
        max_loss_r=4.0,
        stop_r=None,
        loss_model="scaled",
        loss_floor_r=1.0,
        max_trades_per_day=3,
        entry_cutoff_trades=3,
        late_trade_tp_mult=None,
        daily_stop_r=None,
        tp_r_seq=None,
        warmup_skip_first_trade=True,
    )

    summary = _write_summary(tmp_path, params, monkeypatch)
    sim_params = summary["params"]

    assert sim_params["effective_max_trades_per_day"] == 2
    assert sim_params["tp_targets_used"] == [0.35, 0.35]
