# src/bot/exit_planner/slack_fmt.py
from __future__ import annotations

from starship_engine.apps.exit_planner.types import ExitPlan


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


def format_exit_plan_slack(plan: ExitPlan, prefix: str) -> str:
    if plan.tier is None:
        return f"{prefix} Exit Plan: disabled"

    lines = []
    lines.append(f"{prefix} Exit Plan (Tier {plan.tier} | score {plan.score}/5)")
    if plan.target_band_pct and plan.primary_target_pct is not None:
        lo, hi = plan.target_band_pct
        lines.append(f"Target: {lo}-{hi}% (suggest {plan.primary_target_pct}%)")

    if plan.eta.enabled and plan.eta.to_15_pct and plan.eta.to_25_pct:
        lines.append(
            "ETA: 15% ~ {eta15} | 25% ~ {eta25}".format(
                eta15=_fmt_range(plan.eta.to_15_pct),
                eta25=_fmt_range(plan.eta.to_25_pct),
            )
        )
        if plan.eta.confidence:
            lines.append(f"Confidence: {plan.eta.confidence.capitalize()}")
    else:
        reason = plan.eta.reason or "disabled"
        lines.append(f"ETA: ({reason})")

    if plan.reasons:
        lines.append("Reasons: " + ", ".join(plan.reasons))

    return "\n".join(lines)
