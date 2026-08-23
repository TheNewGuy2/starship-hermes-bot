from __future__ import annotations

import csv
import io
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable

from starship_web.pine_models import PineTradeState


CALIBRATION_AUX_ALIASES: dict[str, tuple[str, ...]] = {
    "vix1d": ("vix1d",),
    "vix": ("vix",),
    "vix9d": ("vix9d",),
    "vvix": ("vvix",),
    "skew": ("skew",),
    "add": ("add",),
    "vx1": ("vx1",),
    "vx2": ("vx2",),
    "ivrv": ("ivrv",),
    "pop_between": ("pop_between",),
    "regime": ("regime",),
    "surface_edge": ("surface_edge",),
    "surface_stress": ("surface_stress",),
    "surface_skew": ("surface_skew",),
    "vol_comp_ratio": ("vol_comp_ratio",),
    "z_trigger": ("zTrigger", "z_trigger"),
    "z_shape": ("zShape", "z_shape"),
    "z_trend": ("zTrend", "z_trend"),
    "z_vwap_recenter": ("zVWAPRecenter", "z_vwap_recenter"),
    "z_body": ("zBody", "z_body"),
    "z_short_risk": ("zShortRisk", "z_short_risk"),
    "mins_from_open": ("mins_from_open",),
    "mins_left_rth": ("mins_left_rth",),
    "afternoon": ("afternoon",),
    "entry_width": ("entry_width",),
    "entry_put_width": ("entry_put_width",),
    "entry_call_width": ("entry_call_width",),
    "entry_sigma": ("entry_sigma",),
    "entry_center": ("entry_center",),
    "entry_profile": ("entry_profile",),
    "entry_profile_code": ("entry_profile_code",),
    "entry_profile_strength": ("entry_profile_strength",),
    "entry_profile_confidence": ("entry_profile_confidence",),
    "entry_quality_score": ("entry_quality_score",),
    "model_mark_debit": ("model_mark_debit", "modelMarkDebitPts"),
    "model_close_debit": ("model_close_debit", "modelCloseDebitPts"),
    "model_capture_frac": ("model_capture_frac", "modelCaptureFrac"),
    "model_time_left_frac": ("model_time_left_frac", "modelTimeLeftFrac"),
    "model_risk_norm": ("model_risk_norm", "modelRiskNorm"),
    "model_center_quality": ("model_center_quality", "modelCenterQuality"),
    "model_put_threat": ("model_put_threat", "modelPutThreat"),
    "model_call_threat": ("model_call_threat", "modelCallThreat"),
    "model_put_far_otm": ("model_put_far_otm", "modelPutFarOtm"),
    "model_call_far_otm": ("model_call_far_otm", "modelCallFarOtm"),
    "model_put_near_short": ("model_put_near_short", "modelPutNearShort"),
    "model_call_near_short": ("model_call_near_short", "modelCallNearShort"),
    "model_late_gamma": ("model_late_gamma", "modelLateGammaScore"),
    "model_surface_stress": ("model_surface_stress", "modelSurfaceStressNow"),
    "model_fast_tape": ("model_fast_tape", "modelFastTapeScore"),
    "model_background_stress": (
        "model_background_stress",
        "modelBackgroundStressScore",
    ),
    "model_put_extrinsic_frac": (
        "model_put_extrinsic_frac",
        "modelPutExtrinsicFrac",
    ),
    "model_call_extrinsic_frac": (
        "model_call_extrinsic_frac",
        "modelCallExtrinsicFrac",
    ),
    "candidate_credit_model": ("candidate_credit_model",),
    "candidate_time_adj": ("candidate_time_adj",),
    "candidate_far_adj": ("candidate_far_adj",),
    "candidate_far_score": ("candidate_far_score",),
}


CSV_FIELDS = [
    "state_id",
    "strategy_name",
    "position_key",
    "event_type",
    "reason",
    "event_window",
    "bar_time",
    "processed_at",
    "source",
    "calibration_kind",
    "target_entry_credit",
    "open_mid_credit",
    "open_natural_credit",
    "entry_mid_gap",
    "entry_natural_gap",
    "entry_mid_ratio",
    "entry_natural_ratio",
    "target_close_debit",
    "close_mid_debit",
    "close_natural_debit",
    "close_mid_gap",
    "close_natural_gap",
    "close_mid_ratio",
    "close_natural_ratio",
    "pnl_mid_points",
    "pnl_natural_points",
    "short_put",
    "long_put",
    "short_call",
    "long_call",
    "regime",
    "ivrv",
    "pop_between",
    "surface_edge",
    "surface_stress",
    "surface_skew",
    "vol_comp_ratio",
    "z_trigger",
    "z_shape",
    "z_trend",
    "z_vwap_recenter",
    "z_body",
    "z_short_risk",
    "model_time_left_frac",
    "model_risk_norm",
    "model_center_quality",
    "model_put_threat",
    "model_call_threat",
    "model_put_near_short",
    "model_call_near_short",
    "model_background_stress",
    "aux_basis",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_float(value: object) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except Exception:
        return None


def _round_price(value: object) -> float | None:
    number = _as_float(value)
    if number is None:
        return None
    return round(number, 4)


def _gap(actual: object, target: object) -> float | None:
    actual_number = _as_float(actual)
    target_number = _as_float(target)
    if actual_number is None or target_number is None:
        return None
    return _round_price(actual_number - target_number)


def _ratio(actual: object, target: object) -> float | None:
    actual_number = _as_float(actual)
    target_number = _as_float(target)
    if actual_number is None or target_number in (None, 0):
        return None
    return _round_price(actual_number / target_number)


def _event_reason(event: dict[str, object]) -> str:
    note = str(event.get("note") or "").strip()
    if note:
        return note
    event_id = str(event.get("event_id") or "").strip()
    parts = event_id.split("|")
    if len(parts) >= 2 and parts[1]:
        return parts[1]
    return event_id or "n/a"


def _aux_snapshot(
    state: PineTradeState,
    event: dict[str, object],
) -> tuple[dict[str, object], str]:
    event_aux = event.get("aux")
    if isinstance(event_aux, dict) and event_aux:
        return dict(event_aux), "event_aux"
    return dict(state.latest_aux or {}), "state_latest_aux"


def _aux_value(aux: dict[str, object], aliases: tuple[str, ...]) -> object | None:
    for key in aliases:
        if key in aux:
            return aux[key]
    return None


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def _infer_event_window(
    event: dict[str, object],
    reason: str,
    aux: dict[str, object],
    active_window: str | None,
) -> str | None:
    afternoon = _as_bool(_aux_value(aux, ("afternoon",)))
    if afternoon is True:
        return "PM"
    if afternoon is False:
        return "AM"

    haystack = f"{event.get('event_id') or ''}|{reason}".upper()
    if "ENTRY_PM" in haystack or "|PM" in haystack or "_PM" in haystack:
        return "PM"
    if "ENTRY_AM" in haystack or "|AM" in haystack or "_AM" in haystack:
        return "AM"
    return active_window


def _event_strikes(
    state: PineTradeState,
    event: dict[str, object],
) -> dict[str, object]:
    strikes = event.get("strikes")
    if isinstance(strikes, dict) and strikes:
        return strikes
    return state.current_strikes or {}


def _calibration_kind(row: dict[str, object]) -> str:
    if row.get("entry_mid_gap") is not None or row.get("entry_natural_gap") is not None:
        return "entry_credit"
    if row.get("close_mid_gap") is not None or row.get("close_natural_gap") is not None:
        return "close_debit"
    if row.get("close_mid_debit") is not None or row.get("open_mid_credit") is not None:
        return "live_mark_snapshot"
    return "uncalibrated"


def build_pine_calibration_rows(
    states: Iterable[PineTradeState],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for state in states:
        active_window: str | None = None
        for event in state.event_log or []:
            if not isinstance(event, dict):
                continue
            price = event.get("price")
            if not isinstance(price, dict):
                price = {}
            aux, aux_basis = _aux_snapshot(state, event)
            reason = _event_reason(event)
            event_type = str(event.get("event_type") or "n/a").upper()
            event_window = _infer_event_window(event, reason, aux, active_window)
            strikes = _event_strikes(state, event)

            target_entry = _round_price(price.get("target_entry_credit"))
            target_close = _round_price(price.get("target_close_debit"))
            open_mid = _round_price(price.get("current_open_mid_credit"))
            open_natural = _round_price(price.get("current_open_natural_credit"))
            close_mid = _round_price(price.get("close_mid_debit"))
            close_natural = _round_price(price.get("close_natural_debit"))

            entry_mid_gap = _round_price(price.get("target_entry_mid_gap"))
            if entry_mid_gap is None:
                entry_mid_gap = _gap(open_mid, target_entry)
            entry_natural_gap = _round_price(price.get("target_entry_natural_gap"))
            if entry_natural_gap is None:
                entry_natural_gap = _gap(open_natural, target_entry)
            close_mid_gap = _round_price(price.get("target_close_mid_gap"))
            if close_mid_gap is None:
                close_mid_gap = _gap(close_mid, target_close)
            close_natural_gap = _round_price(price.get("target_close_natural_gap"))
            if close_natural_gap is None:
                close_natural_gap = _gap(close_natural, target_close)

            row: dict[str, object] = {
                "state_id": state.state_id,
                "strategy_name": state.strategy_name,
                "position_key": state.position_key,
                "underlier": state.underlier,
                "option_root": state.option_root,
                "event_id": event.get("event_id"),
                "event_type": event_type,
                "reason": reason,
                "event_window": event_window or "n/a",
                "bar_time": event.get("bar_time"),
                "processed_at": event.get("processed_at"),
                "quantity": event.get("quantity", state.quantity),
                "source": price.get("source"),
                "price_error": price.get("error"),
                "display_label": price.get("display_label"),
                "display_value": _round_price(price.get("display_value")),
                "entry_credit_reference": _round_price(
                    price.get("entry_credit_reference")
                ),
                "target_entry_credit": target_entry,
                "open_mid_credit": open_mid,
                "open_natural_credit": open_natural,
                "entry_mid_gap": entry_mid_gap,
                "entry_natural_gap": entry_natural_gap,
                "entry_mid_ratio": _ratio(open_mid, target_entry),
                "entry_natural_ratio": _ratio(open_natural, target_entry),
                "target_close_debit": target_close,
                "close_mid_debit": close_mid,
                "close_natural_debit": close_natural,
                "close_mid_gap": close_mid_gap,
                "close_natural_gap": close_natural_gap,
                "close_mid_ratio": _ratio(close_mid, target_close),
                "close_natural_ratio": _ratio(close_natural, target_close),
                "pnl_mid_points": _round_price(price.get("pnl_mid_points")),
                "pnl_natural_points": _round_price(price.get("pnl_natural_points")),
                "pnl_mid_usd": _round_price(price.get("pnl_mid_usd")),
                "pnl_natural_usd": _round_price(price.get("pnl_natural_usd")),
                "short_put": _round_price(strikes.get("short_put")),
                "long_put": _round_price(strikes.get("long_put")),
                "short_call": _round_price(strikes.get("short_call")),
                "long_call": _round_price(strikes.get("long_call")),
                "aux_basis": aux_basis,
            }
            for out_key, aliases in CALIBRATION_AUX_ALIASES.items():
                row[out_key] = _aux_value(aux, aliases)

            row["calibration_kind"] = _calibration_kind(row)
            rows.append(row)

            if event_type == "ENTRY" and event_window in {"AM", "PM"}:
                active_window = event_window
            if event_type == "CLOSE":
                active_window = None

    return sorted(
        rows,
        key=lambda row: str(row.get("processed_at") or row.get("bar_time") or ""),
    )


def _metric_stats(rows: list[dict[str, object]], key: str) -> dict[str, object]:
    values = [
        number
        for number in (_as_float(row.get(key)) for row in rows)
        if number is not None
    ]
    if not values:
        return {"count": 0, "avg": None, "median": None, "min": None, "max": None}
    return {
        "count": len(values),
        "avg": _round_price(statistics.fmean(values)),
        "median": _round_price(statistics.median(values)),
        "min": _round_price(min(values)),
        "max": _round_price(max(values)),
    }


def _summarize_rows(rows: list[dict[str, object]]) -> dict[str, object]:
    metric_keys = [
        "entry_mid_gap",
        "entry_natural_gap",
        "entry_mid_ratio",
        "entry_natural_ratio",
        "close_mid_gap",
        "close_natural_gap",
        "close_mid_ratio",
        "close_natural_ratio",
    ]
    return {
        "count": len(rows),
        "calibratable_count": sum(
            1
            for row in rows
            if row.get("entry_mid_gap") is not None
            or row.get("entry_natural_gap") is not None
            or row.get("close_mid_gap") is not None
            or row.get("close_natural_gap") is not None
        ),
        "metrics": {key: _metric_stats(rows, key) for key in metric_keys},
    }


def _group_rows(
    rows: list[dict[str, object]],
    key: str,
    *,
    limit: int | None = None,
) -> dict[str, dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        value = str(row.get(key) or "n/a")
        grouped[value].append(row)
    ordered = sorted(grouped.items(), key=lambda item: len(item[1]), reverse=True)
    if limit is not None:
        ordered = ordered[:limit]
    return {name: _summarize_rows(group_rows) for name, group_rows in ordered}


def _recommendations(summary: dict[str, object]) -> list[str]:
    recs: list[str] = []
    overall = summary.get("overall")
    metrics = overall.get("metrics") if isinstance(overall, dict) else {}
    if not isinstance(metrics, dict):
        return recs

    entry_mid = metrics.get("entry_mid_gap")
    close_mid = metrics.get("close_mid_gap")
    entry_count = (
        int(entry_mid.get("count") or 0) if isinstance(entry_mid, dict) else 0
    )
    close_count = int(close_mid.get("count") or 0) if isinstance(close_mid, dict) else 0
    entry_median = (
        _as_float(entry_mid.get("median")) if isinstance(entry_mid, dict) else None
    )
    close_median = (
        _as_float(close_mid.get("median")) if isinstance(close_mid, dict) else None
    )

    if entry_count == 0 and close_count == 0:
        recs.append(
            "No live-vs-Pine price gaps are available yet. Send ENTRY and CLOSE alerts with target_entry_credit or target_close_debit while E*TRADE chain refresh is working."
        )
        return recs

    if entry_count and entry_median is not None:
        if entry_median > 0.10:
            recs.append(
                f"ENTRY live mid credit is above Pine target by median {entry_median:.2f} pts. Candidate credit is probably conservative; calibrate creditPct/candidate discounts or use bridge live credit as the entry basis."
            )
        elif entry_median < -0.10:
            recs.append(
                f"ENTRY live mid credit is below Pine target by median {entry_median:.2f} pts. Pine is over-crediting entries; reduce creditPct or tighten quote-based entry gating."
            )
        else:
            recs.append(
                "ENTRY target credit is close to live mid on the current sample. Keep collecting before changing entry-credit assumptions."
            )

    if close_count and close_median is not None:
        if close_median > 0.10:
            recs.append(
                f"CLOSE live mid debit is above Pine target by median {close_median:.2f} pts. Pine marks are too cheap; bucket by near-short, background stress, and time-left to tune markThetaPow, threat, and stress-carry inputs."
            )
        elif close_median < -0.10:
            recs.append(
                f"CLOSE live mid debit is below Pine target by median {close_median:.2f} pts. Pine marks are too rich; reduce stress/threat carry after confirming fills."
            )
        else:
            recs.append(
                "CLOSE target debit is close to live mid on the current sample. Focus next on natural-fill gaps and state-specific buckets."
            )

    if entry_count + close_count < 20:
        recs.append(
            "Sample size is still small. Treat these as direction-finding signals until you have 20+ calibratable ENTRY/CLOSE events across AM, PM, calm, and stressed states."
        )

    return recs


def summarize_pine_calibration(rows: list[dict[str, object]]) -> dict[str, object]:
    aux_fallback_count = sum(
        1 for row in rows if row.get("aux_basis") == "state_latest_aux"
    )
    summary: dict[str, object] = {
        "overall": _summarize_rows(rows),
        "by_event_type": _group_rows(rows, "event_type"),
        "by_reason": _group_rows(rows, "reason", limit=12),
        "by_event_window": _group_rows(rows, "event_window"),
        "aux_fallback_count": aux_fallback_count,
    }
    summary["recommendations"] = _recommendations(summary)
    return summary


def build_pine_calibration_report(
    states: Iterable[PineTradeState],
) -> dict[str, object]:
    rows = build_pine_calibration_rows(states)
    return {
        "ok": True,
        "generated_at": _utc_now_iso(),
        "row_count": len(rows),
        "summary": summarize_pine_calibration(rows),
        "rows": rows,
    }


def pine_calibration_csv(rows: list[dict[str, object]]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return out.getvalue()
