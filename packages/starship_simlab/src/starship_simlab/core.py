# src/bot/alpha_sim_lab/core.py
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class ConfusionMatrix:
    tp: int
    fp: int
    tn: int
    fn: int


@dataclass(frozen=True)
class RunResult:
    plugin: str
    version: str
    params: dict[str, Any]
    date_start: str
    date_end: str
    total_days: int
    confusion: ConfusionMatrix
    metrics: dict[str, Any]
    output_dir: Path
    schema_version: int = 1
    computed_at: str = ""
    warmup_dropped_days: int = 0
    formulas: dict[str, str] = field(default_factory=dict)


def compute_confusion(bot_ok: list[bool], good_day: list[bool]) -> ConfusionMatrix:
    tp = fp = tn = fn = 0
    for ok, good in zip(bot_ok, good_day):
        if ok and good:
            tp += 1
        elif ok and not good:
            fp += 1
        elif (not ok) and (not good):
            tn += 1
        else:
            fn += 1
    return ConfusionMatrix(tp=tp, fp=fp, tn=tn, fn=fn)


def _max_streak(seq: list[bool], target: bool) -> int:
    best = 0
    cur = 0
    for v in seq:
        if v == target:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def max_streak(seq: list[bool], target: bool) -> int:
    return _max_streak(seq, target)


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def compute_metrics(bot_ok: list[bool], good_day: list[bool]) -> dict[str, Any]:
    total = len(bot_ok)
    trade_days = sum(1 for v in bot_ok if v)
    tp = sum(1 for ok, good in zip(bot_ok, good_day) if ok and good)
    fp = sum(1 for ok, good in zip(bot_ok, good_day) if ok and not good)
    tn = sum(1 for ok, good in zip(bot_ok, good_day) if (not ok) and (not good))
    fn = sum(1 for ok, good in zip(bot_ok, good_day) if (not ok) and good)

    baseline_wins = sum(1 for good in good_day if good)

    trade_results = [good for ok, good in zip(bot_ok, good_day) if ok]
    max_win_streak = _max_streak(trade_results, True) if trade_results else 0
    max_loss_streak = _max_streak(trade_results, False) if trade_results else 0

    return {
        "total_days": total,
        "trade_days": trade_days,
        "trade_rate": (trade_days / total) if total else 0.0,
        "win_rate_on_bot_days": (tp / trade_days) if trade_days else 0.0,
        "precision_on_bot_days": (tp / (tp + fp)) if (tp + fp) else 0.0,
        "recall_good_days": (tp / (tp + fn)) if (tp + fn) else 0.0,
        "recall_bad_days": (tn / (tn + fp)) if (tn + fp) else 0.0,
        "baseline_win_rate": (baseline_wins / total) if total else 0.0,
        "bad_day_avoidance_rate": (tn / (tn + fp)) if (tn + fp) else 0.0,
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
    }


def ensure_output_dir(base: Optional[str], plugin: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_path = Path(base) if base else Path("sim_lab/runs")
    out_dir = base_path / plugin / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def write_summary(path: Path, result: RunResult) -> None:
    computed_at = result.computed_at or datetime.now().isoformat(timespec="seconds")
    formulas = result.formulas or {
        "bad_day_avoidance_rate": "tn / (tn + fp)",
        "recall_good_days": "tp / (tp + fn)",
        "recall_bad_days": "tn / (tn + fp)",
        "precision_on_bot_days": "tp / (tp + fp)",
        "win_rate_on_bot_days": "tp / trade_days",
        "baseline_win_rate": "good_days / total_days",
    }
    payload = {
        "schema_version": result.schema_version,
        "computed_at": computed_at,
        "plugin": result.plugin,
        "version": result.version,
        "params": result.params,
        "date_start": result.date_start,
        "date_end": result.date_end,
        "total_days": result.total_days,
        "warmup_dropped_days": result.warmup_dropped_days,
        "confusion": result.confusion.__dict__,
        "metrics": result.metrics,
        "formulas": formulas,
    }
    (path / "summary.json").write_text(json.dumps(payload, indent=2) + "\n")


def write_daily_csv(
    path: Path, rows: list[dict[str, Any]], filename: str = "daily.csv"
) -> Path:
    if not rows:
        return path / filename
    out_path = path / filename
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return out_path


def write_daily_parquet(
    path: Path, rows: list[dict[str, Any]], filename: str = "daily.parquet"
) -> Path | None:
    if not rows:
        return None
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except Exception:
        return None
    out_path = path / filename
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, out_path)
    return out_path
