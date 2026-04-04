# src/bot/report.py
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter, MinuteLocator
from matplotlib.lines import Line2D
from starship_shared.sentinel import MarketSnapshot, SentinelEventType

from starship_engine.apps.sentinel.core import SentinelParams, SlackSentinel
from starship_engine.apps.sentinel.state import StateParams, derive_market_state

TZ_CST = ZoneInfo("America/Chicago")


@dataclass
class Series:
    times: list[datetime]
    close: list[float]
    ema21: list[float]
    iv_atm: list[float]
    rv30: list[float]
    rv60: list[float]
    rv90: list[float]
    vrp: list[float]
    mr: list[float]
    vov: list[float]
    gate_ok: list[bool]
    gate_reason: list[Optional[str]]
    early_ok: list[Optional[bool]]
    slack_sent: list[Optional[bool]]
    greeks_symbols: list[int]
    exit_tier: list[Optional[str]]
    exit_primary: list[Optional[float]]
    exit_eta_enabled: list[Optional[bool]]
    context_regime: list[Optional[str]]
    shadow_guard_active: list[Optional[bool]]
    shadow_gate_ok: list[Optional[bool]]
    shadow_vrp: list[Optional[float]]
    shadow_rv60_cap: list[Optional[float]]
    max_abs_5m_return: list[Optional[float]]
    trend_score: list[Optional[float]]
    rows_total: int
    rows_deduped: int
    duplicates_removed: int


@dataclass
class DerivedState:
    score: list[int]
    regime: list[str]
    data_ready: list[bool]


def _parse_ts(ts: str, tz: ZoneInfo) -> Optional[datetime]:
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=TZ_CST).astimezone(tz)
    except Exception:
        return None


def _to_float(value) -> float:
    try:
        return float(value)
    except Exception:
        return float("nan")


def _to_int(value) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _is_finite(val: float) -> bool:
    return isinstance(val, (int, float)) and math.isfinite(val)


def load_series(path: Path, tz: ZoneInfo) -> Series:
    rows: list[tuple] = []

    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue

            dt = _parse_ts(str(row.get("ts_cst", "")), tz)
            if not dt:
                continue

            exit_plan = row.get("exit_plan") if isinstance(row, dict) else None
            exit_tier = None
            exit_primary = None
            exit_eta_enabled = None
            if isinstance(exit_plan, dict):
                exit_tier = exit_plan.get("tier")
                exit_primary = exit_plan.get("primary_target_pct")
                eta = exit_plan.get("eta")
                if isinstance(eta, dict):
                    exit_eta_enabled = eta.get("enabled")

            rows.append(
                (
                    dt,
                    _to_float(row.get("close")),
                    _to_float(row.get("ema21")),
                    _to_float(row.get("iv_atm")),
                    _to_float(row.get("rv30")),
                    _to_float(row.get("rv60")),
                    _to_float(row.get("rv90")),
                    _to_float(row.get("vrp")),
                    _to_float(row.get("mr")),
                    _to_float(row.get("vov")),
                    bool(row.get("gate_ok", False)),
                    row.get("gate_reason"),
                    row.get("early_ok", None),
                    row.get("slack_sent", None),
                    _to_int(row.get("greeks_symbols")),
                    exit_tier,
                    _to_float(exit_primary)
                    if exit_primary is not None
                    else float("nan"),
                    exit_eta_enabled,
                    row.get("context_regime"),
                    row.get("shadow_guard_active"),
                    row.get("shadow_gate_ok"),
                    _to_float(row.get("shadow_vrp")),
                    _to_float(row.get("shadow_rv60_cap")),
                    _to_float(row.get("max_abs_5m_return")),
                    _to_float(
                        (row.get("trend") or {}).get("risk_score")
                        if isinstance(row, dict)
                        else None
                    ),
                )
            )

    rows_total = len(rows)
    # De-duplicate timestamps by keeping the last row for each time.
    latest_by_time: dict[datetime, tuple] = {}
    for row in rows:
        latest_by_time[row[0]] = row

    deduped = latest_by_time
    rows_deduped = len(deduped)
    duplicates_removed = max(0, rows_total - rows_deduped)

    times: list[datetime] = []
    close: list[float] = []
    ema21: list[float] = []
    iv_atm: list[float] = []
    rv30: list[float] = []
    rv60: list[float] = []
    rv90: list[float] = []
    vrp: list[float] = []
    mr: list[float] = []
    vov: list[float] = []
    gate_ok: list[bool] = []
    gate_reason: list[Optional[str]] = []
    early_ok: list[Optional[bool]] = []
    slack_sent: list[Optional[bool]] = []
    greeks_symbols: list[int] = []
    exit_tier: list[Optional[str]] = []
    exit_primary: list[Optional[float]] = []
    exit_eta_enabled: list[Optional[bool]] = []
    context_regime: list[Optional[str]] = []
    shadow_guard_active: list[Optional[bool]] = []
    shadow_gate_ok: list[Optional[bool]] = []
    shadow_vrp: list[Optional[float]] = []
    shadow_rv60_cap: list[Optional[float]] = []
    max_abs_5m_return: list[Optional[float]] = []
    trend_score: list[Optional[float]] = []

    for _, row in sorted(deduped.items(), key=lambda x: x[0]):
        times.append(row[0])
        close.append(row[1])
        ema21.append(row[2])
        iv_atm.append(row[3])
        rv30.append(row[4])
        rv60.append(row[5])
        rv90.append(row[6])
        vrp.append(row[7])
        mr.append(row[8])
        vov.append(row[9])
        gate_ok.append(row[10])
        gate_reason.append(row[11])
        early_ok.append(row[12])
        slack_sent.append(row[13])
        greeks_symbols.append(row[14])
        exit_tier.append(row[15])
        exit_primary.append(None if math.isnan(row[16]) else row[16])
        exit_eta_enabled.append(row[17])
        context_regime.append(row[18])
        shadow_guard_active.append(row[19])
        shadow_gate_ok.append(row[20])
        shadow_vrp.append(None if math.isnan(row[21]) else row[21])
        shadow_rv60_cap.append(None if math.isnan(row[22]) else row[22])
        max_abs_5m_return.append(None if math.isnan(row[23]) else row[23])
        trend_score.append(None if math.isnan(row[24]) else row[24])

    return Series(
        times=times,
        close=close,
        ema21=ema21,
        iv_atm=iv_atm,
        rv30=rv30,
        rv60=rv60,
        rv90=rv90,
        vrp=vrp,
        mr=mr,
        vov=vov,
        gate_ok=gate_ok,
        gate_reason=gate_reason,
        early_ok=early_ok,
        slack_sent=slack_sent,
        greeks_symbols=greeks_symbols,
        exit_tier=exit_tier,
        exit_primary=exit_primary,
        exit_eta_enabled=exit_eta_enabled,
        context_regime=context_regime,
        shadow_guard_active=shadow_guard_active,
        shadow_gate_ok=shadow_gate_ok,
        shadow_vrp=shadow_vrp,
        shadow_rv60_cap=shadow_rv60_cap,
        max_abs_5m_return=max_abs_5m_return,
        trend_score=trend_score,
        rows_total=rows_total,
        rows_deduped=rows_deduped,
        duplicates_removed=duplicates_removed,
    )


def _shade_gate(ax, times, gate_ok):
    if not times:
        return
    for i, ok in enumerate(gate_ok):
        if ok:
            ax.axvspan(times[i], times[i], color="#cfe8cc", alpha=0.6)


def _derive_state(series: Series) -> DerivedState:
    state_params = StateParams()
    score: list[int] = []
    regime: list[str] = []
    data_ready: list[bool] = []

    for i in range(len(series.times)):
        snap = MarketSnapshot(
            ts=series.times[i],
            symbol="SPX",
            timeframe="5m",
            iv_atm=series.iv_atm[i] if _is_finite(series.iv_atm[i]) else None,
            rv30=series.rv30[i],
            rv60=series.rv60[i],
            rv90=series.rv90[i],
            vrp=series.vrp[i],
            vov=series.vov[i],
            mr=series.mr[i],
            adx=None,
            extra={},
        )
        state = derive_market_state(snap, state_params)
        score.append(state.score)
        regime.append(state.regime_label)

        iv_ok = _is_finite(series.iv_atm[i])
        rv_ok = _is_finite(series.rv60[i]) and series.rv60[i] > 0
        greeks_ok = series.greeks_symbols[i] >= 200
        data_ready.append(iv_ok and rv_ok and greeks_ok)

    return DerivedState(score=score, regime=regime, data_ready=data_ready)


def _derive_events(series: Series) -> list[SentinelEventType]:
    sentinel = SlackSentinel(
        params=SentinelParams(
            score_thresholds=[70, 50, 30],
            hysteresis=5,
            vrp_min=1.25,
            mr_max=0.05,
            vov_caution=0.25,
            vov_hostile=0.35,
            vov_hysteresis=0.02,
            cooldown_regime_change_seconds=600,
            cooldown_score_cross_seconds=600,
            cooldown_vrp_seconds=600,
            cooldown_vov_seconds=600,
            cooldown_mr_seconds=600,
        ),
        state_params=StateParams(),
        tz=TZ_CST,
        enabled=False,
        rth_only=False,
        heartbeat_enabled=False,
        heartbeat_minutes=15,
        heartbeat_rth_only=False,
    )

    events: list[tuple[datetime, str]] = []
    for i in range(len(series.times)):
        iv_ok = _is_finite(series.iv_atm[i])
        rv_ok = _is_finite(series.rv60[i]) and series.rv60[i] > 0
        greeks_ok = series.greeks_symbols[i] >= 200
        if not (iv_ok and rv_ok and greeks_ok):
            continue
        snap = MarketSnapshot(
            ts=series.times[i],
            symbol="SPX",
            timeframe="5m",
            iv_atm=series.iv_atm[i] if _is_finite(series.iv_atm[i]) else None,
            rv30=series.rv30[i],
            rv60=series.rv60[i],
            rv90=series.rv90[i],
            vrp=series.vrp[i],
            vov=series.vov[i],
            mr=series.mr[i],
            adx=None,
            extra={},
        )
        for ev in sentinel.on_candle_close(snap):
            if ev.type != SentinelEventType.HEARTBEAT:
                events.append((ev.ts, ev.type))
    return events


def _plot(
    series: Series,
    title: str,
    out_path: Path,
    summary: dict[str, object] | None = None,
) -> None:
    derived = _derive_state(series)
    events = _derive_events(series)

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 10), sharex=True)
    fig.suptitle(title)

    ax1.plot(series.times, series.close, label="Close", color="#111", linewidth=2.0)
    ax1.plot(series.times, series.ema21, label="EMA21", color="#1f77b4", linewidth=1.8)
    ax1.set_ylabel("Price", fontweight="bold")
    line_legend = ax1.legend(loc="upper left")
    ax1.add_artist(line_legend)

    _shade_gate(ax1, series.times, series.gate_ok)

    ax2.plot(
        series.times,
        [v * 100.0 for v in series.iv_atm],
        label="IV_ATM %",
        color="#9467bd",
    )
    ax2.plot(
        series.times, [v * 100.0 for v in series.rv60], label="RV60 %", color="#2ca02c"
    )
    ax2.plot(series.times, series.vrp, label="VRP", color="#ff7f0e")
    ax2.plot(series.times, series.mr, label="MR", color="#d62728")
    ax2.plot(series.times, series.vov, label="VoV", color="#8c564b")
    ax2.set_ylabel("Metrics", fontweight="bold")
    ax2b = ax2.twinx()
    if any(v is not None for v in series.trend_score):
        ax2b.plot(
            series.times,
            [v if v is not None else float("nan") for v in series.trend_score],
            label="Trend Risk",
            color="#17becf",
            linestyle="--",
        )
        ax2b.set_ylabel("Trend Risk")

    handles, labels = ax2.get_legend_handles_labels()
    handles2, labels2 = ax2b.get_legend_handles_labels()
    ax2.legend(
        handles + handles2, labels + labels2, loc="upper left", ncol=3, fontsize=8
    )

    ax3.plot(series.times, derived.score, label="Score", color="#333")
    ax3.axhline(70, color="#999", linestyle="--", linewidth=0.8)
    ax3.axhline(50, color="#bbb", linestyle=":", linewidth=0.8)
    ax3.axhline(30, color="#ddd", linestyle=":", linewidth=0.8)
    ax3.set_ylabel("Score / Exit Target", fontweight="bold")

    ax3b = ax3.twinx()
    if any(v is not None for v in series.exit_primary):
        ax3b.plot(
            series.times,
            [v if v is not None else float("nan") for v in series.exit_primary],
            label="Exit primary %",
            color="#1f77b4",
            alpha=0.7,
        )
    ax3b.set_ylabel("Exit Target %")

    for i, ok in enumerate(series.early_ok):
        if ok is True:
            ax2.scatter(
                series.times[i], series.vrp[i], color="#1f77b4", s=12, marker="^"
            )

    for i, ok in enumerate(series.gate_ok):
        if ok:
            ax1.scatter(
                series.times[i], series.close[i], color="#2ca02c", s=10, marker="o"
            )

    for i, sent in enumerate(series.slack_sent):
        if sent:
            ax1.scatter(
                series.times[i], series.close[i], color="#1f77b4", s=18, marker="*"
            )

    # Event strip (sentinel triggers) pinned to axes coords so it doesn't compress price
    y_base = 0.02
    y_step = 0.03
    transform = ax1.get_xaxis_transform()
    event_rows = {
        SentinelEventType.REGIME_CHANGE: 0,
        SentinelEventType.SCORE_CROSS: 1,
        SentinelEventType.VRP_BREAK: 2,
        SentinelEventType.VOV_SPIKE: 3,
        SentinelEventType.MR_BREAK: 4,
    }
    for ts, ev_type in events:
        color = "#ff7f0e"
        marker = "x"
        if ev_type == SentinelEventType.REGIME_CHANGE:
            color = "#d62728"
            marker = "s"
        elif ev_type == SentinelEventType.SCORE_CROSS:
            color = "#ff7f0e"
            marker = "x"
        elif ev_type == SentinelEventType.VRP_BREAK:
            color = "#9467bd"
            marker = "v"
        elif ev_type == SentinelEventType.VOV_SPIKE:
            color = "#8c564b"
            marker = "^"
        elif ev_type == SentinelEventType.MR_BREAK:
            color = "#2ca02c"
            marker = "d"
        row = event_rows.get(ev_type, 0)
        y = y_base + (row * y_step)
        ax1.scatter(
            ts, y, color=color, s=18, marker=marker, transform=transform, clip_on=False
        )

    marker_handles = [
        Line2D(
            [0],
            [0],
            marker="o",
            color="w",
            markerfacecolor="#2ca02c",
            label="Gate OK",
            markersize=6,
        ),
        Line2D(
            [0],
            [0],
            marker="^",
            color="w",
            markerfacecolor="#1f77b4",
            label="Early OK",
            markersize=6,
        ),
        Line2D(
            [0],
            [0],
            marker="*",
            color="w",
            markerfacecolor="#1f77b4",
            label="Signal Sent",
            markersize=8,
        ),
        Line2D(
            [0],
            [0],
            marker="s",
            color="w",
            markerfacecolor="#d62728",
            label="Regime Change",
            markersize=6,
        ),
        Line2D(
            [0], [0], marker="x", color="#ff7f0e", label="Score Cross", markersize=6
        ),
        Line2D([0], [0], marker="v", color="#9467bd", label="VRP Break", markersize=6),
        Line2D([0], [0], marker="^", color="#8c564b", label="VoV Spike", markersize=6),
        Line2D([0], [0], marker="d", color="#2ca02c", label="MR Break", markersize=6),
    ]
    ax1.legend(handles=marker_handles, loc="upper right", fontsize=7, title="Markers")

    locator = MinuteLocator(byminute=range(0, 60, 15))
    formatter = DateFormatter(
        "%-I:%M", tz=series.times[0].tzinfo if series.times else TZ_CST
    )
    ax3.xaxis.set_major_locator(locator)
    ax3.xaxis.set_major_formatter(formatter)

    ax1.tick_params(axis="both", labelsize=10)
    ax2.tick_params(axis="both", labelsize=9)
    ax3.tick_params(axis="both", labelsize=9)

    ax2.grid(True, alpha=0.2)
    ax1.grid(True, alpha=0.2)
    ax3.grid(True, alpha=0.2)

    if summary:

        def _fmt_counts(counts: dict[str, int]) -> str:
            if not counts:
                return "n/a"
            total_local = sum(counts.values()) or 1
            parts = []
            for key, val in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
                parts.append(f"{key}: {val} ({val / total_local:.0%})")
            return " | ".join(parts)

        gate_reasons = summary.get("gate_reasons", {})
        regimes = summary.get("regimes", {})
        context_regimes = summary.get("context_regimes", {})

        max_abs_5m = summary.get("max_abs_5m_return", 0.0)
        rv60_max = summary.get("rv60_max", 0.0)
        rv60_raw_max = summary.get("rv60_raw_max", 0.0)
        shadow_guard_rate = summary.get("shadow_guard_rate", 0.0)
        shadow_gate_ok_rate = summary.get("shadow_gate_ok_rate", 0.0)

        text = (
            "Drivers\n"
            f"Shadow: guard={shadow_guard_rate:.1%} | gate_ok={shadow_gate_ok_rate:.1%}\n"
            f"Gate: {_fmt_counts(gate_reasons)}\n"
            f"Regime: {_fmt_counts(regimes)}\n"
            f"Context: {_fmt_counts(context_regimes)}\n"
            f"RV60 diag: max5m={max_abs_5m:.2%} | rv60_max={rv60_max:.2f} | raw={rv60_raw_max:.4f}"
        )
        fig.text(
            0.01,
            0.005,
            text,
            ha="left",
            va="bottom",
            fontsize=8,
            family="monospace",
        )

    fig.tight_layout(rect=[0, 0.07, 1, 0.96])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def _summary(series: Series) -> dict[str, float]:
    total = len(series.times)
    gate_ok = sum(1 for x in series.gate_ok if x)
    early_ok = sum(1 for x in series.early_ok if x is True)

    shadow_guard = sum(1 for x in series.shadow_guard_active if x is True)
    shadow_gate_ok = sum(1 for x in series.shadow_gate_ok if x is True)

    returns: list[float] = []
    for i in range(1, len(series.close)):
        prev = series.close[i - 1]
        cur = series.close[i]
        if prev and _is_finite(prev) and _is_finite(cur):
            returns.append((cur / prev) - 1.0)
    abs_returns = [abs(r) for r in returns if _is_finite(r)]
    max_abs_5m = max(abs_returns) if abs_returns else 0.0
    median_abs_5m = sorted(abs_returns)[len(abs_returns) // 2] if abs_returns else 0.0

    rv60_vals = [v for v in series.rv60 if _is_finite(v)]
    rv60_max = max(rv60_vals) if rv60_vals else 0.0
    rv60_med = sorted(rv60_vals)[len(rv60_vals) // 2] if rv60_vals else 0.0
    ann_factor = math.sqrt(252 * 78)  # 5m bars per trading year
    rv60_raw_max = (rv60_max / ann_factor) if ann_factor > 0 else 0.0

    gate_reason_counts: dict[str, int] = {}
    for reason in series.gate_reason:
        if not reason:
            continue
        gate_reason_counts[reason] = gate_reason_counts.get(reason, 0) + 1

    derived = _derive_state(series)
    regime_counts: dict[str, int] = {}
    for label in derived.regime:
        if not label:
            continue
        regime_counts[label] = regime_counts.get(label, 0) + 1

    context_counts: dict[str, int] = {}
    for label in series.context_regime:
        if not label:
            continue
        context_counts[label] = context_counts.get(label, 0) + 1

    tier_counts = {"A": 0, "B": 0, "C": 0}
    eta_enabled = 0
    for t, eta in zip(series.exit_tier, series.exit_eta_enabled):
        if t in tier_counts:
            tier_counts[t] += 1
        if eta is True:
            eta_enabled += 1

    return {
        "bars": total,
        "gate_ok": gate_ok,
        "gate_ok_rate": (gate_ok / total) if total else 0.0,
        "early_ok": early_ok,
        "early_ok_rate": (early_ok / total) if total else 0.0,
        "exit_tier_a": tier_counts["A"],
        "exit_tier_b": tier_counts["B"],
        "exit_tier_c": tier_counts["C"],
        "exit_eta_enabled": eta_enabled,
        "shadow_guard_active": shadow_guard,
        "shadow_guard_rate": (shadow_guard / total) if total else 0.0,
        "shadow_gate_ok": shadow_gate_ok,
        "shadow_gate_ok_rate": (shadow_gate_ok / total) if total else 0.0,
        "max_abs_5m_return": max_abs_5m,
        "median_abs_5m_return": median_abs_5m,
        "rv60_max": rv60_max,
        "rv60_median": rv60_med,
        "rv60_raw_max": rv60_raw_max,
        "gate_reasons": gate_reason_counts,
        "regimes": regime_counts,
        "context_regimes": context_counts,
        "rows_total": series.rows_total,
        "rows_deduped": series.rows_deduped,
        "duplicates_removed": series.duplicates_removed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate daily journal charts from JSONL."
    )
    parser.add_argument("--date", help="Date YYYY-MM-DD (defaults to today CST).")
    parser.add_argument("--input", help="Path to JSONL file (overrides --date).")
    parser.add_argument(
        "--out-dir", default="reports", help="Output directory for charts."
    )
    parser.add_argument(
        "--tz",
        default="America/Chicago",
        help="Timezone for report timestamps (e.g., America/Chicago, America/New_York).",
    )
    args = parser.parse_args()

    if args.input:
        path = Path(args.input)
        if not path.exists():
            raise SystemExit(f"Input not found: {path}")
        date_str = path.stem.replace("spx0dte_state_", "")
    else:
        if args.date:
            date_str = args.date
        else:
            date_str = datetime.now(TZ_CST).strftime("%Y-%m-%d")
        path = Path("data") / f"spx0dte_state_{date_str}.jsonl"
        if not path.exists():
            raise SystemExit(f"Input not found: {path}")

    try:
        tz = ZoneInfo(args.tz)
    except Exception:
        tz = TZ_CST

    series = load_series(path, tz)
    if not series.times:
        raise SystemExit(f"No data in {path}")

    out_dir = Path(args.out_dir)
    summary = _summary(series)
    chart_path = out_dir / f"{date_str}_report.png"
    _plot(series, f"SPX 0DTE Daily Report ({date_str})", chart_path, summary=summary)
    gate_reasons = summary.get("gate_reasons", {})
    regimes = summary.get("regimes", {})
    context_regimes = summary.get("context_regimes", {})

    def _fmt_counts(counts: dict[str, int]) -> str:
        if not counts:
            return "n/a"
        total_local = sum(counts.values()) or 1
        parts = []
        for key, val in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            parts.append(f"{key}: {val} ({val / total_local:.1%})")
        return " | ".join(parts)

    summary_path = out_dir / f"{date_str}_summary.txt"
    summary_path.write_text(
        "\n".join(
            [
                f"rows_total={summary['rows_total']}",
                f"rows_deduped={summary['rows_deduped']}",
                f"duplicates_removed={summary['duplicates_removed']}",
                f"bars={summary['bars']}",
                f"gate_ok={summary['gate_ok']} ({summary['gate_ok_rate']:.2%})",
                f"early_ok={summary['early_ok']} ({summary['early_ok_rate']:.2%})",
                f"exit_tier_a={summary['exit_tier_a']}",
                f"exit_tier_b={summary['exit_tier_b']}",
                f"exit_tier_c={summary['exit_tier_c']}",
                f"exit_eta_enabled={summary['exit_eta_enabled']}",
                f"shadow_guard_active={summary['shadow_guard_active']} ({summary['shadow_guard_rate']:.2%})",
                f"shadow_gate_ok={summary['shadow_gate_ok']} ({summary['shadow_gate_ok_rate']:.2%})",
                f"max_abs_5m_return={summary['max_abs_5m_return']:.4%}",
                f"median_abs_5m_return={summary['median_abs_5m_return']:.4%}",
                f"rv60_max={summary['rv60_max']:.6f}",
                f"rv60_median={summary['rv60_median']:.6f}",
                f"rv60_raw_max={summary['rv60_raw_max']:.6f}",
                f"gate_reasons={_fmt_counts(gate_reasons)}",
                f"regimes={_fmt_counts(regimes)}",
                f"context_regimes={_fmt_counts(context_regimes)}",
            ]
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
