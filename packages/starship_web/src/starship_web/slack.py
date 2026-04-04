from __future__ import annotations

import os
from typing import Any

import requests
from starship_shared.policy import TradePolicy

from starship_web.policy_format import format_policy_lines
from starship_web.sentinel_format import (
    format_sentinel_batch,
    format_sentinel_event,
    format_sentinel_event_blocks,
)


def get_slack_webhook_url() -> str:
    return os.environ.get("SLACK_WEBHOOK_URL", "").strip()


def send_slack_message(
    message: str | dict[str, Any], *, webhook_url: str | None = None
) -> bool:
    url = get_slack_webhook_url()
    if not url:
        return False
    payload = {"text": message} if isinstance(message, str) else message
    resp = requests.post(url, json=payload, timeout=5)
    return resp.status_code < 300


def _fmt_pct(val: Any) -> str:
    if not isinstance(val, (int, float)):
        return "n/a"
    return f"{val * 100:.2f}%"


def _fmt_float(val: Any, fmt: str) -> str:
    if not isinstance(val, (int, float)):
        return "n/a"
    return format(val, fmt)


def _fmt_range(ranges: dict[str, list[int]] | None) -> str:
    if not ranges:
        return "n/a"
    vals = []
    for key in ("flat", "down", "up"):
        r = ranges.get(key)
        if not r:
            continue
        vals.extend(r)
    if not vals:
        return "n/a"
    return f"{min(vals)}-{max(vals)}m"


def format_exit_plan_from_dict(plan: dict[str, Any], prefix: str) -> str:
    if plan.get("tier") is None:
        return f"{prefix} Exit Plan: disabled"

    lines = []
    lines.append(
        f"{prefix} Exit Plan (Tier {plan.get('tier')} | score {plan.get('score')}/5)"
    )

    target_band = plan.get("target_band_pct")
    primary_target = plan.get("primary_target_pct")
    if target_band and primary_target is not None:
        lo, hi = target_band
        lines.append(f"Target: {lo}-{hi}% (suggest {primary_target}%)")

    eta = plan.get("eta") or {}
    if eta.get("enabled") and eta.get("to_15_pct") and eta.get("to_25_pct"):
        lines.append(
            "ETA: 15% ~ {eta15} | 25% ~ {eta25}".format(
                eta15=_fmt_range(eta.get("to_15_pct")),
                eta25=_fmt_range(eta.get("to_25_pct")),
            )
        )
        if eta.get("confidence"):
            lines.append(f"Confidence: {str(eta.get('confidence')).capitalize()}")
    else:
        reason = eta.get("reason") or "disabled"
        lines.append(f"ETA: ({reason})")

    reasons = plan.get("reasons") or []
    if reasons:
        lines.append("Reasons: " + ", ".join(reasons))

    return "\n".join(lines)


def _build_signal_blocks(fact: dict[str, Any]) -> dict[str, Any]:
    metrics = fact.get("metrics", {})
    state = fact.get("state", {})
    signal = fact.get("signal", {}) or {}
    market = fact.get("market", {}) or {}

    ts_cst = state.get("ts_cst") or fact.get("ts", "")
    underlier = market.get("underlier", "")
    timeframe = market.get("timeframe", "")
    touch = signal.get("touch", False)
    ema_note = "touch" if touch else "no-touch"
    alert_only = bool(signal.get("alert_only", state.get("dry_run", True)))

    header = f"{underlier} 0DTE IC Signal ({ts_cst})"
    alert_line = (
        "⚠️ *Alert only* (no auto-trading)."
        if alert_only
        else "✅ Auto-trading enabled."
    )

    close_val = _fmt_float(metrics.get("close"), ".2f")
    ema21_val = _fmt_float(metrics.get("ema21"), ".2f")
    iv_atm = _fmt_pct(metrics.get("iv_atm"))
    rv60 = _fmt_pct(metrics.get("rv60"))
    vrp = _fmt_float(metrics.get("vrp"), ".2f")
    mr = _fmt_float(metrics.get("mr"), "+.4f")
    vov = _fmt_float(metrics.get("vov"), ".6f")

    iv_disp = _fmt_pct(metrics.get("iv_disp"))
    atr5 = _fmt_float(metrics.get("atr5"), ".2f")
    market_md = (
        f"*Market* ({underlier} {timeframe})\n"
        f"• *Close:* `{close_val}`  |  *EMA21:* `{ema21_val}` _({ema_note})_\n"
        f"• *IV_ATM:* `{iv_atm}`  |  *IV Disp:* `{iv_disp}`  |  *RV60:* `{rv60}`\n"
        f"• *VRP:* `{vrp}`  |  *MR:* `{mr}`  |  *VoV:* `{vov}`  |  *ATR5:* `{atr5}`"
    )

    sp = signal.get("short_put")
    lp = signal.get("long_put")
    sc = signal.get("short_call")
    lc = signal.get("long_call")
    wings = None
    if isinstance(sp, (int, float)) and isinstance(lp, (int, float)):
        wings = abs(sp - lp)
    if wings is None and isinstance(sc, (int, float)) and isinstance(lc, (int, float)):
        wings = abs(sc - lc)
    wing_txt = f"${int(wings)}" if isinstance(wings, (int, float)) else "n/a"
    net_delta = _fmt_float(signal.get("net_short_delta"), "+.3f")

    condor_md = (
        f"*Condor*  _(wings: {wing_txt}, target: ~20Δ)_\n"
        f"• *PUT:*  `Short {_fmt_float(sp, '.0f')}` / `Long {_fmt_float(lp, '.0f')}`\n"
        f"• *CALL:* `Short {_fmt_float(sc, '.0f')}` / `Long {_fmt_float(lc, '.0f')}`\n"
        f"• *NetΔ (shorts):* `{net_delta}`"
    )

    trend = signal.get("trend") or state.get("trend")
    if isinstance(trend, dict):
        score = trend.get("risk_score")
        tier = trend.get("risk_tier")
        adx = trend.get("adx14")
        adx_slope = trend.get("adx_slope_3")
        di_plus = trend.get("di_plus14")
        di_minus = trend.get("di_minus14")
        di_spread = trend.get("di_spread")
        stretch = trend.get("stretch_atr")
        comps = trend.get("components") or {}
        comp_line = ""
        if isinstance(comps, dict) and comps:
            comp_line = (
                f"\n• *Components:* "
                f"adx_lvl={comps.get('adx_level_pts')} "
                f"adx_slope={comps.get('adx_slope_pts')} "
                f"di_dom={comps.get('di_dom_pts')} "
                f"stretch={comps.get('stretch_pts')} "
                f"rv_mod={comps.get('rv_mod')}"
            )
        trend_md = (
            f"*Risk / Trend*\n"
            f"• *Trend Risk:* `{score}/10 ({tier})`\n"
            f"• *ADX:* `{_fmt_float(adx, '.1f')}`  |  "
            f"*ADX slope:* `{_fmt_float(adx_slope, '.2f')}`\n"
            f"• *DI+* `{_fmt_float(di_plus, '.1f')}`  |  "
            f"*DI-* `{_fmt_float(di_minus, '.1f')}`  |  "
            f"*DI spread:* `{_fmt_float(di_spread, '.1f')}`\n"
            f"• *Stretch:* `{_fmt_float(stretch, '.2f')}`"
            f"{comp_line}"
        )
    else:
        trend_md = "*Risk / Trend*\n• Trend Risk: `n/a`"

    policy_payload = signal.get("policy") or state.get("policy")
    policy_lines: list[str] = []
    if isinstance(policy_payload, dict):
        try:
            policy = TradePolicy.model_validate(policy_payload)
        except Exception:
            policy = None
        if policy:
            policy_lines = format_policy_lines(policy)
    else:
        policy_summary = signal.get("policy_summary")
        policy_flat_by = signal.get("policy_flat_by")
        if policy_summary:
            policy_lines.append(policy_summary)
        if policy_flat_by:
            policy_lines.append(policy_flat_by)

    policy_name = ""
    if isinstance(policy_payload, dict):
        policy_name = str(policy_payload.get("name") or "")

    policy_md = "*Trade Policy*"
    if policy_lines:
        if policy_name:
            policy_md += f"  `{policy_name}`"
        policy_md += "\n" + "\n".join(f"• {line}" for line in policy_lines)
    else:
        policy_md += "\n• n/a"

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": header}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": alert_line}]},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": market_md}},
        {"type": "section", "text": {"type": "mrkdwn", "text": condor_md}},
        {"type": "section", "text": {"type": "mrkdwn", "text": trend_md}},
        {"type": "section", "text": {"type": "mrkdwn", "text": policy_md}},
    ]
    exit_plan = signal.get("exit_plan")
    if isinstance(exit_plan, dict):
        exit_md = format_exit_plan_from_dict(exit_plan, "*Exit Plan*")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": exit_md}})
    return {"text": header, "blocks": blocks}


def format_signal_message(fact: dict[str, Any]) -> dict[str, Any]:
    return _build_signal_blocks(fact)


def format_fact_message(fact: dict[str, Any]) -> str | dict[str, Any]:
    kind = fact.get("kind", "FACT")
    if kind == "STRATEGY_SIGNAL":
        return format_signal_message(fact)
    if kind == "ENGINE_EVENT":
        return format_engine_event_message(fact)

    run_id = fact.get("run_id", "")
    seq = fact.get("seq", "")
    ts = fact.get("ts", "")
    market = fact.get("market", {})
    underlier = market.get("underlier", "")
    timeframe = market.get("timeframe", "")

    header = f"{kind} {underlier} {timeframe}".strip()
    return f"{header}\nrun={run_id} seq={seq} ts={ts}"


def format_engine_event_message(fact: dict[str, Any]) -> str:
    events = fact.get("events") or []
    if not events:
        return format_fact_message({**fact, "kind": "FACT"})

    event = events[0]
    if event.get("type"):
        policy = None
        policy_payload = (fact.get("state") or {}).get("policy")
        if isinstance(policy_payload, dict):
            try:
                policy = TradePolicy.model_validate(policy_payload)
            except Exception:
                policy = None
        if len(events) > 1:
            return format_sentinel_batch(events, policy)
        return format_sentinel_event_blocks(event, policy)
    name = event.get("name") or event.get("type") or "event"
    text = event.get("text")
    if text:
        return text
    payload = event.get("payload") or {}

    if name == "startup":
        return (
            ":rocket: Bot starting (dry_run={dry_run}, warmup_bars={warmup_bars}, "
            "ignore_time_window={ignore_time_window}).".format(
                dry_run=payload.get("dry_run", False),
                warmup_bars=payload.get("warmup_bars", 0),
                ignore_time_window=payload.get("ignore_time_window", False),
            )
        )
    if name == "warmup_complete":
        return f":white_check_mark: Bot warmup complete ({payload.get('bars', 0)} closed bars)."
    if name == "stream_idle":
        idle = payload.get("idle_seconds", 0)
        return f":warning: Stream idle for {idle:.0f}s (no candles/quotes/greeks)."
    if name == "crash":
        err = payload.get("exit_error", "")
        return f":rotating_light: *Bot crashed*: `{err}`"
    if name == "shutdown":
        exit_reason = payload.get("exit_reason", "unknown")
        stats = payload.get("stats") or {}
        return (
            f":stop_sign: Bot stopping ({exit_reason}). "
            f"bars={stats.get('bars', 0)} touch={stats.get('touch', 0)} "
            f"gate_ok={stats.get('gate_ok', 0)} data_ready={stats.get('data_ready', 0)} "
            f"condor_built={stats.get('condor_built', 0)} slack_sent={stats.get('slack_sent', 0)}"
        )

    # Fallback for unknown events
    return f"ENGINE_EVENT {name}"
