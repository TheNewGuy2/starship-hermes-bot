# src/bot/pipeline/alpha_sim_lab_handler.py
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from starship_simlab.core import RunResult
from starship_simlab.ic_filter import IcFilterParams, run_ic_filter


@dataclass
class AlphaSimLabHandler:
    output_dir: Optional[str] = "sim_lab/runs"

    def run_one(self, params: dict[str, Any], outdir: Path) -> dict[str, Any]:
        plugin_name = params.get("plugin", "alpha_sim_lab.ic_filter")
        if not str(plugin_name).startswith("alpha_sim_lab."):
            raise ValueError("plugin must start with 'alpha_sim_lab.'")
        if plugin_name != "alpha_sim_lab.ic_filter":
            raise ValueError(f"Unsupported plugin: {plugin_name}")

        start = _parse_date_param(params.get("start"))
        end = _parse_date_param(params.get("end"))
        ic_params = IcFilterParams(
            start=start,
            end=end,
            ic_range=float(params.get("ic_range")),
            vrp_min=float(params.get("vrp_min", 1.25)),
            vrp_max=_opt_float(params.get("vrp_max")),
            rv60_min=float(params.get("rv60_min", 0.05)),
            mr_max=float(params.get("mr_max", 0.05)),
            vov_max=_opt_float(params.get("vov_max")),
            symbol=params.get("symbol", "^GSPC"),
            vix_symbol=params.get("vix_symbol", "^VIX"),
            data_source=params.get("data_source", "yahoo"),
            input_csv=params.get("input_csv"),
            vix_csv=params.get("vix_csv"),
            mode=params.get("mode", "classify"),
            output_format=params.get("output_format", "auto"),
            phase=float(params.get("phase", 2.0)),
            tp_r=float(params.get("tp_r", 0.25)),
            max_loss_r=float(params.get("max_loss_r", 4.0)),
            stop_r=_opt_float(params.get("stop_r")),
            loss_model=params.get("loss_model", "scaled"),
            loss_floor_r=float(params.get("loss_floor_r", 1.0)),
            max_trades_per_day=int(params.get("max_trades_per_day", 1)),
            entry_cutoff_trades=int(params.get("entry_cutoff_trades", 2)),
            late_trade_tp_mult=_opt_float(params.get("late_trade_tp_mult")),
            daily_stop_r=_opt_float(params.get("daily_stop_r")),
            tp_r_grid=params.get("tp_r_grid"),
            tp_r_seq=params.get("tp_r_seq"),
            warmup_entry_mode=params.get("warmup_entry_mode", "slot_proxy"),
            warmup_skip_first_trade=bool(params.get("warmup_skip_first_trade", True)),
            earliest_entry_time_cst=params.get("earliest_entry_time_cst", "10:50"),
        )
        run_ic_filter(ic_params, out_dir=str(outdir))
        summary_path = outdir / "summary.json"
        return json.loads(summary_path.read_text())

    def run_ic_filter(
        self,
        *,
        start: date,
        end: date,
        ic_range: float,
        vrp_min: float,
        vrp_max: Optional[float],
        rv60_min: float,
        mr_max: float,
        vov_max: Optional[float],
        symbol: str = "^GSPC",
        vix_symbol: str = "^VIX",
        data_source: str = "yahoo",
        vix_source: Optional[str] = None,
        input_csv: Optional[str] = None,
        vix_csv: Optional[str] = None,
        mode: str = "classify",
        output_format: str = "auto",
        phase: float = 2.0,
        tp_r: float = 0.25,
        max_loss_r: float = 4.0,
        stop_r: Optional[float] = None,
        loss_model: str = "scaled",
        loss_floor_r: float = 1.0,
        max_trades_per_day: int = 1,
        entry_cutoff_trades: int = 2,
        late_trade_tp_mult: Optional[float] = None,
        daily_stop_r: Optional[float] = None,
        tp_r_seq: Optional[list[float]] = None,
        tp_r_grid: Optional[str] = None,
        warmup_entry_mode: str = "slot_proxy",
        warmup_skip_first_trade: bool = True,
        earliest_entry_time_cst: Optional[str] = "10:50",
        out_dir: Optional[str] = None,
    ) -> RunResult:
        params = IcFilterParams(
            start=start,
            end=end,
            ic_range=ic_range,
            vrp_min=vrp_min,
            vrp_max=vrp_max,
            rv60_min=rv60_min,
            mr_max=mr_max,
            vov_max=vov_max,
            symbol=symbol,
            vix_symbol=vix_symbol,
            data_source=data_source,
            vix_source=vix_source,
            input_csv=input_csv,
            vix_csv=vix_csv,
            mode=mode,
            output_format=output_format,
            phase=phase,
            tp_r=tp_r,
            max_loss_r=max_loss_r,
            stop_r=stop_r,
            loss_model=loss_model,
            loss_floor_r=loss_floor_r,
            max_trades_per_day=max_trades_per_day,
            entry_cutoff_trades=entry_cutoff_trades,
            late_trade_tp_mult=late_trade_tp_mult,
            daily_stop_r=daily_stop_r,
            tp_r_seq=tp_r_seq,
            tp_r_grid=tp_r_grid,
            warmup_entry_mode=warmup_entry_mode,
            warmup_skip_first_trade=warmup_skip_first_trade,
            earliest_entry_time_cst=earliest_entry_time_cst,
        )
        return run_ic_filter(params, out_base=self.output_dir, out_dir=out_dir)


def _parse_date_param(val: Any) -> date:
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        return datetime.strptime(val, "%Y-%m-%d").date()
    raise ValueError("start/end must be YYYY-MM-DD")


def _opt_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, str) and val.strip().lower() in ("none", "null", ""):
        return None
    return float(val)
