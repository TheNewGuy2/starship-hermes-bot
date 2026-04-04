# src/bot/pipeline/alpha_sim_lab/suite_runner.py
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Any, Optional

import yaml

from starship_engine.domain.alpha_sim_lab.suite_schema import SuiteConfig
from starship_engine.domain.alpha_sim_lab_handler import AlphaSimLabHandler


@dataclass(frozen=True)
class RunSpec:
    test_name: str
    run_name: str
    params: dict[str, Any]


def _load_suite_config(path: Path) -> SuiteConfig:
    data = yaml.safe_load(path.read_text())
    return SuiteConfig(**data)


def _expand_sweep(
    test_name: str, params: dict[str, Any], sweep: dict[str, list[Any]]
) -> list[RunSpec]:
    keys = list(sweep.keys())
    values = [sweep[k] for k in keys]
    runs: list[RunSpec] = []
    for combo in product(*values):
        combo_params = dict(params)
        run_parts = []
        for k, v in zip(keys, combo):
            combo_params[k] = v
            run_parts.append(f"{k}={v}")
        run_name = ",".join(run_parts) if run_parts else "base"
        runs.append(
            RunSpec(test_name=test_name, run_name=run_name, params=combo_params)
        )
    return runs


def _runs_from_suite(cfg: SuiteConfig) -> list[RunSpec]:
    runs: list[RunSpec] = []
    for test in cfg.tests:
        merged = {**cfg.defaults, **test.params}
        if test.sweep:
            runs.extend(_expand_sweep(test.name, merged, test.sweep))
        else:
            runs.append(RunSpec(test_name=test.name, run_name="base", params=merged))
    return runs


def _write_summary_rows(out_dir: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    csv_path = out_dir / "suite_summary.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    (out_dir / "suite_summary.json").write_text(json.dumps(rows, indent=2) + "\n")


def run_suite(
    config_path: Path,
    *,
    outdir: Optional[Path] = None,
    only: Optional[str] = None,
    fail_fast: bool = False,
    dry_run: bool = False,
) -> Path:
    cfg = _load_suite_config(config_path)
    runs = _runs_from_suite(cfg)
    if only:
        runs = [r for r in runs if r.test_name == only]

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_out = outdir or Path("sim_lab/runs/suites") / cfg.suite_name / ts
    base_out.mkdir(parents=True, exist_ok=True)
    (base_out / "suite_config.yaml").write_text(config_path.read_text())

    plugin = AlphaSimLabHandler(output_dir=None)
    summary_rows: list[dict[str, Any]] = []
    for run in runs:
        run_dir = base_out / "runs" / run.test_name / run.run_name
        run_dir.mkdir(parents=True, exist_ok=True)
        if dry_run:
            continue
        try:
            summary = plugin.run_one(run.params, run_dir)
        except Exception as exc:
            if fail_fast:
                raise
            summary = {"error": str(exc)}
        pnl = summary.get("metrics", {}).get("pnl", {})
        params = summary.get("params", {})
        row = {
            "test_name": run.test_name,
            "run_name": run.run_name,
            "ic_range": run.params.get("ic_range"),
            "tp_r": run.params.get("tp_r"),
            "tp_r_seq": run.params.get("tp_r_seq"),
            "max_trades_per_day": run.params.get("max_trades_per_day"),
            "entry_cutoff_trades": run.params.get("entry_cutoff_trades"),
            "warmup_entry_mode": params.get("warmup_entry_mode"),
            "warmup_skip_first_trade": params.get("warmup_skip_first_trade"),
            "earliest_entry_time_cst": params.get("earliest_entry_time_cst"),
            "effective_max_trades_per_day": params.get("effective_max_trades_per_day"),
            "total_pnl_r": pnl.get("total_pnl_r"),
            "profit_factor": pnl.get("profit_factor"),
            "max_drawdown_r": pnl.get("max_drawdown_r"),
            "trade_rate": summary.get("metrics", {}).get("trade_rate"),
        }
        summary_rows.append(row)

    summary_rows.sort(
        key=lambda r: (
            r.get("total_pnl_r") if r.get("total_pnl_r") is not None else float("-inf"),
            r.get("max_drawdown_r")
            if r.get("max_drawdown_r") is not None
            else float("-inf"),
            r.get("profit_factor")
            if r.get("profit_factor") is not None
            else float("-inf"),
        ),
        reverse=True,
    )
    _write_summary_rows(base_out, summary_rows)

    if summary_rows:
        print("Top 10 runs:")
        for row in summary_rows[:10]:
            print(
                f"{row['test_name']} | {row['run_name']} | pnl={row['total_pnl_r']} "
                f"dd={row['max_drawdown_r']} pf={row['profit_factor']}"
            )

    return base_out
