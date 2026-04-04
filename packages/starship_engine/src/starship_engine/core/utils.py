from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from starship_engine.apps.exit_planner.types import ExitPlan, ExitPlannerConfig


def dt_from_ms(ms: int, tz: ZoneInfo) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=tz)


def is_today(ms: int, tz: ZoneInfo) -> bool:
    return dt_from_ms(ms, tz).date() == datetime.now(tz).date()


def in_signal_window(ms: int, tz: ZoneInfo) -> bool:
    """Signals allowed between 08:35 and 11:00 CST."""
    dt = dt_from_ms(ms, tz)
    if dt.hour < 8 or (dt.hour == 8 and dt.minute < 35):
        return False
    if dt.hour > 11 or (dt.hour == 11 and dt.minute > 0):
        return False
    return True


def utc_iso() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def is_near_live(t_ms: int, minutes: int = 20) -> bool:
    return t_ms >= (utc_now_ms() - int(minutes * 60 * 1000))


def ok_mark(x: bool) -> str:
    return "✅" if x else "❌"


def append_reason(reason: str, extra: str) -> str:
    if not extra:
        return reason
    if reason in ("", "OK"):
        return extra
    if extra in reason:
        return reason
    return f"{reason} | {extra}"


def cap_exit_plan_for_trend(plan: ExitPlan, cfg: ExitPlannerConfig) -> ExitPlan:
    if plan.target_band_pct is None or plan.primary_target_pct is None:
        return plan
    if plan.target_band_pct[1] <= cfg.tier_b_max:
        return plan
    reasons = list(plan.reasons)
    reasons.append("Trend risk cap")
    return ExitPlan(
        tier="B",
        score=plan.score,
        target_band_pct=(cfg.tier_b_min, cfg.tier_b_max),
        primary_target_pct=min(plan.primary_target_pct, cfg.tier_b_max),
        reasons=reasons,
        eta=plan.eta,
    )
