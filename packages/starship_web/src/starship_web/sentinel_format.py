# Slack formatting for sentinel events (web-owned).
from __future__ import annotations

from datetime import datetime
from typing import Any

from starship_shared.policy import TradePolicy, policy_time_hint
from starship_shared.sentinel import SentinelEventType

from starship_web.policy_format import format_policy_lines


def _emoji(event_type: str, severity: str | None) -> str:
    if event_type == SentinelEventType.HEARTBEAT:
        return "🕒"
    if severity == "critical":
        return "🚨"
    if severity == "warn":
        return "⚠️"
    return "ℹ️"


def _parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _fmt_float(val: Any, fmt: str) -> str:
    if not isinstance(val, (int, float)):
        return "n/a"
    return format(val, fmt)


def _format_trend_block(extra: dict[str, Any]) -> str | None:
    trend = extra.get("trend")
    if not isinstance(trend, dict):
        return None
    tier = trend.get("risk_tier")
    score = trend.get("risk_score")
    adx = _fmt_float(trend.get("adx14"), ".1f")
    adx_slope = _fmt_float(trend.get("adx_slope_3"), ".2f")
    di_plus = _fmt_float(trend.get("di_plus14"), ".1f")
    di_minus = _fmt_float(trend.get("di_minus14"), ".1f")
    spread = _fmt_float(trend.get("di_spread"), ".1f")
    stretch = _fmt_float(trend.get("stretch_atr"), ".2f")
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
    if tier is None and score is None:
        return None
    return (
        f"*Risk / Trend*\n"
        f"• *Trend Risk:* `{score}/10 ({tier})`\n"
        f"• *ADX:* `{adx}`  |  *ADX slope:* `{adx_slope}`\n"
        f"• *DI+* `{di_plus}`  |  *DI-* `{di_minus}`  |  *DI spread:* `{spread}`\n"
        f"• *Stretch:* `{stretch}`"
        f"{comp_line}"
    )


def format_sentinel_event_blocks(
    event: dict[str, Any], policy: TradePolicy | None
) -> dict[str, Any]:
    snap = event.get("snapshot") or {}
    state = event.get("state") or {}
    extra = snap.get("extra") or {}

    event_type = event.get("type") or "EVENT"
    severity = event.get("severity")
    ts = _parse_ts(snap.get("ts"))
    time_str = ts.strftime("%H:%M") if ts else "??:??"

    header = (
        f"{_emoji(event_type, severity)} "
        f"{event_type.replace('_', ' ')} | "
        f"{snap.get('symbol', '')} {snap.get('timeframe', '')} {time_str}"
    ).strip()

    changes = event.get("changes") or []
    if (
        event_type in (SentinelEventType.REGIME_CHANGE, SentinelEventType.SCORE_CROSS)
        and changes
    ):
        line2 = changes[0]
    elif event_type == SentinelEventType.HEARTBEAT:
        line2 = f"Regime: {state.get('regime_label')} | Score: {state.get('score')}"
    else:
        line2 = f"Regime: {state.get('regime_label')} | Score {state.get('score')}"

    vrp_txt = _fmt_float(snap.get("vrp"), ".2f")
    rv60_min = extra.get("rv60_min")
    rv60 = snap.get("rv60")
    if isinstance(rv60_min, (int, float)) and isinstance(rv60, (int, float)):
        if rv60 < rv60_min:
            vrp_txt = "n/a (rv60 low)"

    line3 = f"VRP {vrp_txt} | VoV {_fmt_float(snap.get('vov'), '.2f')}"
    line4 = (
        f"RV30 {_fmt_float(snap.get('rv30'), '.2f')} | "
        f"RV60 {_fmt_float(snap.get('rv60'), '.2f')} | "
        f"RV90 {_fmt_float(snap.get('rv90'), '.2f')} | "
        f"MR {_fmt_float(snap.get('mr'), '+.2f')}"
    )
    if extra.get("data_ready") is False:
        line4 += " | Data: not ready"
    flags = ", ".join(state.get("flags") or []) or "none"
    line5 = f"Flags: {flags}"

    trend_md = _format_trend_block(extra) or "*Risk / Trend*\n• Trend Risk: `n/a`"

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": header}},
        {"type": "section", "text": {"type": "mrkdwn", "text": line2}},
        {"type": "section", "text": {"type": "mrkdwn", "text": line3}},
        {"type": "section", "text": {"type": "mrkdwn", "text": line4}},
        {"type": "section", "text": {"type": "mrkdwn", "text": trend_md}},
        {"type": "section", "text": {"type": "mrkdwn", "text": line5}},
    ]

    if policy:
        policy_lines = format_policy_lines(policy)
        policy_md = "*Trade Policy*"
        if policy_lines:
            policy_md += f"  `{policy.name}`\n" + "\n".join(
                f"• {line}" for line in policy_lines
            )
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": policy_md}}
        )
        hint = policy_time_hint(policy, ts or datetime.now())
        if hint:
            blocks.append(
                {"type": "context", "elements": [{"type": "mrkdwn", "text": hint}]}
            )
        gate_ok = extra.get("gate_ok")
        if gate_ok is True:
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": "Eligible to enter. Plan active."}
                    ],
                }
            )
        elif gate_ok is False:
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "No new entries. If in trade, consider reducing/closing.",
                        }
                    ],
                }
            )
        if event_type == SentinelEventType.REGIME_CHANGE and severity in (
            "warn",
            "critical",
        ):
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": "Regime degraded: do not re-enter."}
                    ],
                }
            )

    return {"text": header, "blocks": blocks}


def format_sentinel_event(event: dict[str, Any], policy: TradePolicy | None) -> str:
    snap = event.get("snapshot") or {}
    state = event.get("state") or {}
    extra = snap.get("extra") or {}

    event_type = event.get("type") or "EVENT"
    severity = event.get("severity")
    ts = _parse_ts(snap.get("ts"))
    time_str = ts.strftime("%H:%M") if ts else "??:??"

    header = (
        f"{_emoji(event_type, severity)} "
        f"{event_type.replace('_', ' ')} | "
        f"{snap.get('symbol', '')} {snap.get('timeframe', '')} {time_str}"
    ).strip()

    changes = event.get("changes") or []
    if (
        event_type in (SentinelEventType.REGIME_CHANGE, SentinelEventType.SCORE_CROSS)
        and changes
    ):
        line2 = changes[0]
    elif event_type == SentinelEventType.HEARTBEAT:
        line2 = f"Regime: {state.get('regime_label')} | Score: {state.get('score')}"
    else:
        line2 = f"Regime: {state.get('regime_label')} | Score {state.get('score')}"

    vrp_txt = _fmt_float(snap.get("vrp"), ".2f")
    rv60_min = extra.get("rv60_min")
    rv60 = snap.get("rv60")
    if isinstance(rv60_min, (int, float)) and isinstance(rv60, (int, float)):
        if rv60 < rv60_min:
            vrp_txt = "n/a (rv60 low)"

    line3 = f"VRP {vrp_txt} | VoV {_fmt_float(snap.get('vov'), '.2f')}"
    line4 = (
        f"RV30 {_fmt_float(snap.get('rv30'), '.2f')} | "
        f"RV60 {_fmt_float(snap.get('rv60'), '.2f')} | "
        f"RV90 {_fmt_float(snap.get('rv90'), '.2f')} | "
        f"MR {_fmt_float(snap.get('mr'), '+.2f')}"
    )
    if extra.get("data_ready") is False:
        line4 += " | Data: not ready"
    flags = ", ".join(state.get("flags") or []) or "none"
    line5 = f"Flags: {flags}"

    lines = [header, line2, line3, line4, line5]

    if policy:
        policy_lines = format_policy_lines(policy)
        if policy_lines:
            lines.append(f"Policy: {policy.name}")
            lines.extend(f"- {line}" for line in policy_lines)
        hint = policy_time_hint(policy, ts or datetime.now())
        if hint:
            lines.append(hint)
        gate_ok = extra.get("gate_ok")
        if gate_ok is True:
            lines.append("Eligible to enter. Plan active.")
        elif gate_ok is False:
            lines.append("Not eligible to enter. Gate blocked.")

    return "\n".join(lines)


def format_sentinel_batch(
    events: list[dict[str, Any]], policy: TradePolicy | None
) -> dict[str, Any]:
    if not events:
        return ""

    primary = events[0]
    snap = primary.get("snapshot") or {}
    state = primary.get("state") or {}
    extra = snap.get("extra") or {}

    severity = "info"
    for ev in events:
        if ev.get("severity") == "critical":
            severity = "critical"
            break
        if ev.get("severity") == "warn":
            severity = "warn"

    event_type = "SENTINEL_UPDATE"
    ts = _parse_ts(snap.get("ts"))
    time_str = ts.strftime("%H:%M") if ts else "??:??"
    header = (
        f"{_emoji(event_type, severity)} {event_type.replace('_', ' ')} | "
        f"{snap.get('symbol', '')} {snap.get('timeframe', '')} {time_str}"
    ).strip()

    triggers: list[str] = []
    for ev in events:
        etype = ev.get("type") or "EVENT"
        changes = ev.get("changes") or []
        if changes:
            triggers.append(f"{etype.replace('_', ' ')}: {changes[0]}")
        else:
            triggers.append(etype.replace("_", " "))

    vrp_txt = _fmt_float(snap.get("vrp"), ".2f")
    rv60_min = extra.get("rv60_min")
    rv60 = snap.get("rv60")
    if isinstance(rv60_min, (int, float)) and isinstance(rv60, (int, float)):
        if rv60 < rv60_min:
            vrp_txt = "n/a (rv60 low)"

    line3 = f"VRP {vrp_txt} | VoV {_fmt_float(snap.get('vov'), '.2f')}"
    line4 = (
        f"RV30 {_fmt_float(snap.get('rv30'), '.2f')} | "
        f"RV60 {_fmt_float(snap.get('rv60'), '.2f')} | "
        f"RV90 {_fmt_float(snap.get('rv90'), '.2f')} | "
        f"MR {_fmt_float(snap.get('mr'), '+.2f')}"
    )
    if extra.get("data_ready") is False:
        line4 += " | Data: not ready"
    flags = ", ".join(state.get("flags") or []) or "none"
    line5 = f"Flags: {flags}"

    trend_md = _format_trend_block(extra) or "*Risk / Trend*\n• Trend Risk: `n/a`"
    trigger_md = "*Triggers*\n" + "\n".join(f"• {t}" for t in triggers)

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": header}},
        {"type": "section", "text": {"type": "mrkdwn", "text": trigger_md}},
        {"type": "section", "text": {"type": "mrkdwn", "text": line3}},
        {"type": "section", "text": {"type": "mrkdwn", "text": line4}},
        {"type": "section", "text": {"type": "mrkdwn", "text": trend_md}},
        {"type": "section", "text": {"type": "mrkdwn", "text": line5}},
    ]

    if policy:
        policy_lines = format_policy_lines(policy)
        policy_md = "*Trade Policy*"
        if policy_lines:
            policy_md += f"  `{policy.name}`\n" + "\n".join(
                f"• {line}" for line in policy_lines
            )
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": policy_md}}
        )
        hint = policy_time_hint(policy, ts or datetime.now())
        if hint:
            blocks.append(
                {"type": "context", "elements": [{"type": "mrkdwn", "text": hint}]}
            )
        gate_ok = extra.get("gate_ok")
        if gate_ok is True:
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": "Eligible to enter. Plan active."}
                    ],
                }
            )
        elif gate_ok is False:
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "No new entries. If in trade, consider reducing/closing.",
                        }
                    ],
                }
            )
        if severity in ("warn", "critical") and any(
            (ev.get("type") == SentinelEventType.REGIME_CHANGE) for ev in events
        ):
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": "Regime degraded: do not re-enter."}
                    ],
                }
            )

    return {"text": header, "blocks": blocks}
