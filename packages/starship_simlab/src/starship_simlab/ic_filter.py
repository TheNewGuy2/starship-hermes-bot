# src/bot/alpha_sim_lab/ic_filter.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Optional

from starship_engine.apps.heston.logic import HestonGateParams, evaluate_heston_gate
from starship_engine.indicators import annualized_realized_vol, log_returns

from starship_simlab.core import (
    RunResult,
    clamp,
    compute_confusion,
    compute_metrics,
    ensure_output_dir,
    write_daily_csv,
    write_daily_parquet,
    write_summary,
)
from starship_simlab.data_sources import (
    DailyBar,
    fetch_stooq_daily,
    fetch_yahoo_daily,
    load_daily_csv,
)

PLUGIN_NAME = "alpha_sim_lab.ic_filter"
PLUGIN_VERSION = "0.25"


@dataclass(frozen=True)
class IcFilterParams:
    start: date
    end: date
    ic_range: float
    vrp_min: float
    vrp_max: Optional[float]
    rv60_min: float
    mr_max: float
    vov_max: Optional[float]
    symbol: str = "^gspc"
    vix_symbol: str = "^vix"
    data_source: str = "yahoo"
    vix_source: Optional[str] = None
    input_csv: Optional[str] = None
    vix_csv: Optional[str] = None
    mode: str = "classify"  # classify | pnl
    output_format: str = "auto"  # auto | parquet | csv
    phase: float = 2.0
    tp_r: float = 0.25
    max_loss_r: float = 4.0
    stop_r: Optional[float] = None
    loss_model: str = "scaled"  # binary | scaled
    loss_floor_r: float = 1.0
    max_trades_per_day: int = 1
    entry_cutoff_trades: int = 2
    tp_r_seq: Optional[list[float]] = None
    daily_stop_r: Optional[float] = None
    tp_r_grid: Optional[str] = None
    late_trade_tp_mult: Optional[float] = None
    warmup_entry_mode: str = "slot_proxy"
    warmup_skip_first_trade: bool = True
    earliest_entry_time_cst: Optional[str] = "10:50"
    charts: bool = True


def _build_series(
    bars: list[DailyBar],
) -> tuple[list[date], list[float], list[float], list[float], list[float]]:
    bars = sorted(bars, key=lambda b: b.date)
    dates = [b.date for b in bars]
    opens = [b.open for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    closes = [b.close for b in bars]
    return dates, opens, highs, lows, closes


def _rolling_rv(closes: list[float], window: int) -> list[float]:
    out = [float("nan")] * len(closes)
    for i in range(window, len(closes)):
        rets = log_returns(closes[i - window : i + 1])
        out[i] = annualized_realized_vol(rets, bars_per_day=1)
    return out


def _is_nan(value: float) -> bool:
    return value != value


def _warn(message: str) -> None:
    print(f"[alpha_sim_lab] {message}")


def _write_charts(out_dir: Path, daily_rows: list[dict[str, Any]]) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        _warn("matplotlib not available; skipping charts")
        return

    if not daily_rows:
        return

    equity = [row.get("equity_r", 0.0) for row in daily_rows]
    drawdown = [row.get("drawdown_r", 0.0) for row in daily_rows]
    day_pnl = [row.get("day_pnl_r", 0.0) for row in daily_rows]
    trades = [row.get("trades_taken", 0) for row in daily_rows]

    def _plot(series: list[float], title: str, filename: str) -> None:
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(series, linewidth=1.2)
        ax.set_title(title)
        ax.grid(True, alpha=0.3)
        ax.set_xticks([])
        fig.tight_layout()
        fig.savefig(out_dir / filename, dpi=160)
        plt.close(fig)

    def _plot_bar(series: list[float], title: str, filename: str) -> None:
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.bar(range(len(series)), series, width=0.8)
        ax.set_title(title)
        ax.grid(True, axis="y", alpha=0.3)
        ax.set_xticks([])
        fig.tight_layout()
        fig.savefig(out_dir / filename, dpi=160)
        plt.close(fig)

    _plot(equity, "Equity Curve (R)", "equity.png")
    _plot(drawdown, "Drawdown (R)", "drawdown.png")
    _plot_bar(day_pnl, "Daily PnL (R)", "daily_pnl.png")
    _plot_bar(trades, "Trades Taken", "trades.png")


def _map_symbol_for_source(symbol: str, source: str) -> str:
    sym = symbol.strip()
    if source == "yahoo":
        if sym.lower() in ("^spx", "spx"):
            return "^GSPC"
        if sym.lower() in ("^vix", "vix"):
            return "^VIX"
    if source == "stooq":
        if sym.lower() in ("^gspc", "gspc"):
            return "^spx"
        if sym.lower() in ("^vix", "vix"):
            return "vi.f"
    return sym


def _load_data(params: IcFilterParams) -> tuple[list[DailyBar], list[DailyBar]]:
    if params.input_csv:
        spx = load_daily_csv(params.input_csv)
    else:
        if params.data_source == "stooq":
            spx_sym = _map_symbol_for_source(params.symbol, "stooq")
            spx = fetch_stooq_daily(spx_sym, params.start, params.end)
        elif params.data_source == "yahoo":
            spx_sym = _map_symbol_for_source(params.symbol, "yahoo")
            spx = fetch_yahoo_daily(spx_sym, params.start, params.end)
        else:
            raise ValueError("Only stooq or yahoo data_source is supported in v1.")

    if params.vix_csv:
        vix = load_daily_csv(params.vix_csv)
    else:
        vix_source = params.vix_source or params.data_source
        if vix_source == "stooq":
            vix_sym = _map_symbol_for_source(params.vix_symbol, "stooq")
            vix = fetch_stooq_daily(vix_sym, params.start, params.end)
        elif vix_source == "yahoo":
            vix_sym = _map_symbol_for_source(params.vix_symbol, "yahoo")
            vix = fetch_yahoo_daily(vix_sym, params.start, params.end)
        else:
            raise ValueError("Only stooq or yahoo vix_source is supported in v1.")

    return spx, vix


def run_ic_filter(
    params: IcFilterParams,
    out_base: Optional[str] = None,
    out_dir: Optional[str] = None,
) -> RunResult:
    spx_bars, vix_bars = _load_data(params)
    dates, opens, highs, lows, closes = _build_series(spx_bars)

    vix_map = {b.date: b.close for b in vix_bars}

    rv30 = _rolling_rv(closes, 30)
    rv60 = _rolling_rv(closes, 60)
    rv90 = _rolling_rv(closes, 90)
    vov = [float("nan")] * len(closes)
    warmup_dropped_days = 0

    good_day: list[bool] = []
    bot_ok: list[bool] = []
    daily_rows: list[dict[str, Any]] = []

    gate_params = HestonGateParams(
        enabled=True,
        vrp_min=params.vrp_min,
        vrp_max=params.vrp_max,
        mr_max=params.mr_max,
        rv60_min=params.rv60_min,
        vov_max=None,
    )
    if params.vov_max is not None:
        _warn("vov_max is not supported with yahoo proxy data; ignoring.")

    for i, d in enumerate(dates):
        if d < params.start or d > params.end:
            continue

        iv_atm = None
        vix_close = vix_map.get(d)
        if isinstance(vix_close, (int, float)):
            iv_atm = float(vix_close) / 100.0

        if iv_atm is None or _is_nan(rv30[i]) or _is_nan(rv60[i]) or _is_nan(rv90[i]):
            warmup_dropped_days += 1
            continue

        range_pct = (highs[i] - lows[i]) / opens[i] if opens[i] > 0 else float("nan")
        good = bool(range_pct == range_pct and range_pct <= params.ic_range)

        gate = evaluate_heston_gate(
            enabled=True,
            iv_atm=iv_atm,
            rv30=rv30[i],
            rv60=rv60[i],
            rv90=rv90[i],
            vov=None,
            params=gate_params,
        )

        bot_ok.append(bool(gate.ok))
        good_day.append(good)

        daily_rows.append(
            {
                "date": d.isoformat(),
                "open": opens[i],
                "high": highs[i],
                "low": lows[i],
                "close": closes[i],
                "range_pct": range_pct,
                "good_day": good,
                "gate_ok": gate.ok,
                "gate_reason": gate.reason,
                "iv_atm": iv_atm,
                "rv30": rv30[i],
                "rv60": rv60[i],
                "rv90": rv90[i],
                "vrp": gate.vrp,
                "mr": gate.mr,
                "vov": None,
            }
        )

    confusion = compute_confusion(bot_ok, good_day)
    metrics = compute_metrics(bot_ok, good_day)

    if out_dir:
        out_dir_path = Path(out_dir)
        out_dir_path.mkdir(parents=True, exist_ok=True)
    else:
        out_dir_path = ensure_output_dir(out_base, "ic_filter")

    if params.mode == "pnl":
        pnl_r: list[float] = []
        loss_r_applied: list[float] = []
        tp_hit: list[bool] = []
        stopped_out: list[bool] = []
        trades_taken: list[int] = []
        day_pnl_r: list[float] = []
        hit_daily_stop: list[bool] = []
        base_effective_max = min(params.max_trades_per_day, params.entry_cutoff_trades)
        warmup_skipped = (
            params.warmup_entry_mode == "slot_proxy" and params.warmup_skip_first_trade
        )
        effective_max_trades_per_day = (
            max(0, base_effective_max - 1) if warmup_skipped else base_effective_max
        )
        tp_targets_used: Optional[list[float]] = None
        if params.phase >= 2.5:
            tp_seq = params.tp_r_seq if params.tp_r_seq else []
            tp_targets_used = (
                list(tp_seq)
                if tp_seq
                else [params.tp_r] * max(0, params.max_trades_per_day)
            )
            tp_targets_used = tp_targets_used[:effective_max_trades_per_day]
            if params.late_trade_tp_mult is not None and not tp_seq:
                for i in range(1, len(tp_targets_used)):
                    tp_targets_used[i] = tp_targets_used[i] * params.late_trade_tp_mult

        for ok, good, row in zip(bot_ok, good_day, daily_rows):
            if not ok:
                pnl_r.append(0.0)
                loss_r_applied.append(0.0)
                tp_hit.append(False)
                stopped_out.append(False)
                trades_taken.append(0)
                day_pnl_r.append(0.0)
                hit_daily_stop.append(False)
                continue

            range_pct = row["range_pct"]
            loss_r = params.max_loss_r
            if params.loss_model == "scaled":
                over = max(0.0, float(range_pct) - params.ic_range)
                over_norm = clamp(over / params.ic_range, 0.0, 1.0)
                loss_r = params.loss_floor_r + over_norm * (
                    params.max_loss_r - params.loss_floor_r
                )
            stopped = False
            if params.stop_r is not None:
                loss_r = min(loss_r, params.stop_r)
                stopped = loss_r == params.stop_r

            if params.phase >= 2.5:
                day_total = 0.0
                taken = 0
                hit_stop = False
                tp_seq = params.tp_r_seq if params.tp_r_seq else []
                # warmup_skip_first_trade reduces early opportunity count only.
                # Do not shift TP sequencing; the first trade taken uses tp_r_seq[0].
                tp_targets = (
                    list(tp_seq)
                    if tp_seq
                    else [params.tp_r] * max(0, params.max_trades_per_day)
                )
                tp_targets = tp_targets[:effective_max_trades_per_day]
                for k in range(effective_max_trades_per_day):
                    tp_r_k = tp_targets[k] if k < len(tp_targets) else params.tp_r
                    if params.late_trade_tp_mult is not None and not tp_seq and k >= 1:
                        tp_r_k = tp_r_k * params.late_trade_tp_mult
                    if good:
                        trade_pnl = tp_r_k
                        day_total += trade_pnl
                        taken += 1
                        if (
                            params.daily_stop_r is not None
                            and day_total <= -params.daily_stop_r
                        ):
                            hit_stop = True
                            break
                    else:
                        day_total += -loss_r
                        taken += 1
                        break
                day_pnl_r.append(day_total)
                trades_taken.append(taken)
                hit_daily_stop.append(hit_stop)
                pnl_r.append(day_total)
                loss_r_applied.append(loss_r if not good else 0.0)
                tp_hit.append(good)
                stopped_out.append(stopped)
            else:
                if good:
                    pnl_r.append(params.tp_r)
                    loss_r_applied.append(0.0)
                    tp_hit.append(True)
                    stopped_out.append(False)
                    trades_taken.append(1)
                    day_pnl_r.append(params.tp_r)
                    hit_daily_stop.append(False)
                else:
                    pnl_r.append(-loss_r)
                    loss_r_applied.append(loss_r)
                    tp_hit.append(False)
                    stopped_out.append(stopped)
                    trades_taken.append(1)
                    day_pnl_r.append(-loss_r)
                    hit_daily_stop.append(False)

        equity_r: list[float] = []
        peak_r: list[float] = []
        drawdown_r: list[float] = []
        total = 0.0
        peak = 0.0
        for v in pnl_r:
            total += v
            peak = max(peak, total)
            equity_r.append(total)
            peak_r.append(peak)
            drawdown_r.append(total - peak)

        for row, pnl, loss, tp, stop, eq, dd, taken, day_pnl, hit_stop in zip(
            daily_rows,
            pnl_r,
            loss_r_applied,
            tp_hit,
            stopped_out,
            equity_r,
            drawdown_r,
            trades_taken,
            day_pnl_r,
            hit_daily_stop,
        ):
            row["pnl_r"] = pnl
            row["day_pnl_r"] = day_pnl
            row["equity_r"] = eq
            row["drawdown_r"] = dd
            row["loss_r_applied"] = loss
            row["tp_hit_assumed"] = tp
            row["stopped_out"] = stop
            row["trades_taken"] = taken
            row["hit_daily_stop"] = hit_stop
            row["tp_r_used"] = params.tp_r
            row["tp_r_seq_used"] = (
                ",".join(str(v) for v in params.tp_r_seq) if params.tp_r_seq else None
            )
            row["effective_max_trades"] = effective_max_trades_per_day
            row["warmup_skipped_first_trade"] = warmup_skipped
            row["tp_targets_used"] = (
                ",".join(str(v) for v in tp_targets_used)
                if tp_targets_used is not None
                else None
            )

        trade_pnls = [p for p, ok in zip(day_pnl_r, bot_ok) if ok]
        sum_pos = sum(p for p in day_pnl_r if p > 0)
        sum_neg = sum(p for p in day_pnl_r if p < 0)
        profit_factor = None
        if sum_neg != 0:
            profit_factor = sum_pos / abs(sum_neg)
        elif sum_pos > 0:
            profit_factor = float("inf")

        def _quantile(vals: list[float], q: float) -> Optional[float]:
            if not vals:
                return None
            vals_sorted = sorted(vals)
            idx = int(round((len(vals_sorted) - 1) * q))
            return vals_sorted[idx]

        pnl_metrics = {
            "total_pnl_r": equity_r[-1] if equity_r else 0.0,
            "avg_pnl_r_per_day": (sum(day_pnl_r) / len(day_pnl_r))
            if day_pnl_r
            else 0.0,
            "avg_pnl_r_per_trade": (
                (sum(trade_pnls) / len(trade_pnls)) if trade_pnls else None
            ),
            "profit_factor": profit_factor,
            "max_drawdown_r": min(drawdown_r) if drawdown_r else 0.0,
            "num_wins": sum(1 for p in trade_pnls if p > 0),
            "num_losses": sum(1 for p in trade_pnls if p < 0),
            "win_rate_trades": (
                (sum(1 for p in trade_pnls if p > 0) / len(trade_pnls))
                if trade_pnls
                else 0.0
            ),
            "pnl_trade_quantiles": {
                "p50": _quantile(trade_pnls, 0.5),
                "p10": _quantile(trade_pnls, 0.1),
                "p01": _quantile(trade_pnls, 0.01),
            },
        }
        if params.phase >= 2.5:
            trade_days = [t for t, ok in zip(trades_taken, bot_ok) if ok]
            day_trade_pnls = [p for p, ok in zip(day_pnl_r, bot_ok) if ok]
            pnl_metrics.update(
                {
                    "avg_trades_per_trade_day": (
                        (sum(trade_days) / len(trade_days)) if trade_days else 0.0
                    ),
                    "avg_trades_per_calendar_day": (
                        (sum(trades_taken) / len(trades_taken)) if trades_taken else 0.0
                    ),
                    "pct_trade_days_with_2plus": (
                        (sum(1 for t in trade_days if t >= 2) / len(trade_days))
                        if trade_days
                        else 0.0
                    ),
                    "pct_days_hit_daily_stop": (
                        (
                            sum(1 for h, ok in zip(hit_daily_stop, bot_ok) if ok and h)
                            / len(trade_days)
                        )
                        if trade_days
                        else 0.0
                    ),
                    "best_day_pnl_r": max(day_trade_pnls) if day_trade_pnls else 0.0,
                    "worst_day_pnl_r": min(day_trade_pnls) if day_trade_pnls else 0.0,
                    "day_pnl_trade_quantiles": {
                        "p50": _quantile(day_trade_pnls, 0.5),
                        "p10": _quantile(day_trade_pnls, 0.1),
                        "p01": _quantile(day_trade_pnls, 0.01),
                    },
                }
            )
        metrics["pnl"] = pnl_metrics
    else:
        tp_targets_used = None
    output_format_used = params.output_format
    formulas = {
        "bad_day_avoidance_rate": "tn / (tn + fp)",
        "recall_good_days": "tp / (tp + fn)",
        "recall_bad_days": "tn / (tn + fp)",
        "precision_on_bot_days": "tp / (tp + fp)",
        "win_rate_on_bot_days": "tp / trade_days",
        "baseline_win_rate": "good_days / total_days",
    }
    if params.mode == "pnl":
        formulas.update(
            {
                "total_pnl_r": "sum(pnl_r)",
                "avg_pnl_r_per_day": "mean(pnl_r)",
                "avg_pnl_r_per_trade": "mean(pnl_r | bot_ok)",
                "profit_factor": "sum(pnl_r>0) / abs(sum(pnl_r<0))",
                "max_drawdown_r": "min(equity_r - peak_equity_r)",
            }
        )
        if params.phase >= 2.5:
            formulas.update(
                {
                    "avg_trades_per_trade_day": "mean(trades_taken | bot_ok)",
                    "avg_trades_per_calendar_day": "mean(trades_taken)",
                    "pct_trade_days_with_2plus": "mean(trades_taken>=2 | bot_ok)",
                    "pct_days_hit_daily_stop": "mean(hit_daily_stop | bot_ok)",
                    "best_day_pnl_r": "max(day_pnl_r | bot_ok)",
                    "worst_day_pnl_r": "min(day_pnl_r | bot_ok)",
                }
            )

    wrote_parquet = False
    if params.output_format in ("parquet", "auto"):
        wrote_parquet = write_daily_parquet(
            out_dir_path, daily_rows, filename="daily.parquet"
        )
    if params.output_format == "csv" or not wrote_parquet:
        write_daily_csv(out_dir_path, daily_rows, filename="daily.csv")
        output_format_used = "csv"
    else:
        output_format_used = "parquet"

    if params.mode == "pnl" and params.charts:
        _write_charts(out_dir_path, daily_rows)

    failures = [
        r for r, ok, good in zip(daily_rows, bot_ok, good_day) if ok and not good
    ]
    if failures:
        write_daily_csv(out_dir_path, failures, filename="failures.csv")

    write_summary(
        out_dir_path,
        RunResult(
            plugin=PLUGIN_NAME,
            version=PLUGIN_VERSION,
            params={
                "start": params.start.isoformat(),
                "end": params.end.isoformat(),
                "ic_range": params.ic_range,
                "vrp_min": params.vrp_min,
                "vrp_max": params.vrp_max,
                "rv60_min": params.rv60_min,
                "mr_max": params.mr_max,
                "vov_max": params.vov_max,
                "symbol": params.symbol,
                "vix_symbol": params.vix_symbol,
                "data_source": params.data_source,
                "vix_source": params.vix_source or params.data_source,
                "input_csv": params.input_csv,
                "vix_csv": params.vix_csv,
                "mode": params.mode,
                "output_format": params.output_format,
                "output_format_used": output_format_used,
                "phase": params.phase,
                "tp_r": params.tp_r,
                "max_loss_r": params.max_loss_r,
                "stop_r": params.stop_r,
                "loss_model": params.loss_model,
                "loss_floor_r": params.loss_floor_r,
                "max_trades_per_day": params.max_trades_per_day,
                "entry_cutoff_trades": params.entry_cutoff_trades,
                "late_trade_tp_mult": params.late_trade_tp_mult,
                "daily_stop_r": params.daily_stop_r,
                "tp_r_grid": params.tp_r_grid,
                "tp_r_seq": params.tp_r_seq,
                "warmup_entry_mode": params.warmup_entry_mode,
                "warmup_skip_first_trade": params.warmup_skip_first_trade,
                "earliest_entry_time_cst": params.earliest_entry_time_cst,
                "effective_max_trades_per_day": (
                    max(
                        0,
                        min(params.max_trades_per_day, params.entry_cutoff_trades) - 1,
                    )
                    if (
                        params.warmup_entry_mode == "slot_proxy"
                        and params.warmup_skip_first_trade
                    )
                    else min(params.max_trades_per_day, params.entry_cutoff_trades)
                ),
                "tp_targets_used": tp_targets_used,
            },
            date_start=params.start.isoformat(),
            date_end=params.end.isoformat(),
            total_days=len(bot_ok),
            confusion=confusion,
            metrics=metrics,
            output_dir=out_dir_path,
            warmup_dropped_days=warmup_dropped_days,
            formulas=formulas,
        ),
    )

    return RunResult(
        plugin=PLUGIN_NAME,
        version=PLUGIN_VERSION,
        params={
            "start": params.start.isoformat(),
            "end": params.end.isoformat(),
            "ic_range": params.ic_range,
            "vrp_min": params.vrp_min,
            "vrp_max": params.vrp_max,
            "rv60_min": params.rv60_min,
            "mr_max": params.mr_max,
            "vov_max": params.vov_max,
            "symbol": params.symbol,
            "vix_symbol": params.vix_symbol,
            "data_source": params.data_source,
            "vix_source": params.vix_source or params.data_source,
            "input_csv": params.input_csv,
            "vix_csv": params.vix_csv,
            "mode": params.mode,
            "output_format": params.output_format,
            "output_format_used": output_format_used,
            "phase": params.phase,
            "tp_r": params.tp_r,
            "max_loss_r": params.max_loss_r,
            "stop_r": params.stop_r,
            "loss_model": params.loss_model,
            "loss_floor_r": params.loss_floor_r,
            "max_trades_per_day": params.max_trades_per_day,
            "entry_cutoff_trades": params.entry_cutoff_trades,
            "late_trade_tp_mult": params.late_trade_tp_mult,
            "daily_stop_r": params.daily_stop_r,
            "tp_r_grid": params.tp_r_grid,
            "tp_r_seq": params.tp_r_seq,
            "warmup_entry_mode": params.warmup_entry_mode,
            "warmup_skip_first_trade": params.warmup_skip_first_trade,
            "earliest_entry_time_cst": params.earliest_entry_time_cst,
            "effective_max_trades_per_day": (
                max(
                    0,
                    min(params.max_trades_per_day, params.entry_cutoff_trades) - 1,
                )
                if (
                    params.warmup_entry_mode == "slot_proxy"
                    and params.warmup_skip_first_trade
                )
                else min(params.max_trades_per_day, params.entry_cutoff_trades)
            ),
            "tp_targets_used": tp_targets_used,
        },
        date_start=params.start.isoformat(),
        date_end=params.end.isoformat(),
        total_days=len(bot_ok),
        confusion=confusion,
        metrics=metrics,
        output_dir=out_dir_path,
        warmup_dropped_days=warmup_dropped_days,
        formulas=formulas,
    )
