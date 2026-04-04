# Shared trade policy domain model.
from __future__ import annotations

from datetime import datetime, time
from typing import Optional

from pydantic import BaseModel, Field


class TradePolicy(BaseModel):
    schema_version: int = 1
    name: str = "ic_harvest_v1"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    source: dict = Field(default_factory=dict)

    strategy: str = "0DTE_SPX_IC"
    tp_targets: list[float] = Field(default_factory=lambda: [0.45, 0.25, 0.25])
    max_trades_per_day: int = 3
    entry_cutoff_trades: int = 2
    daily_stop_r: Optional[float] = 1.5
    per_trade_stop_r: Optional[float] = None
    exit_all_by_cst: str = "14:45"
    no_new_entries_after_cst: str = "13:45"

    notes: Optional[str] = None

    def summary_line(self) -> str:
        tp_str = ",".join(f"{int(t * 100)}" for t in self.tp_targets)
        day_stop = f"{self.daily_stop_r}R" if self.daily_stop_r is not None else "n/a"
        return (
            f"Policy {self.name}: TP=[{tp_str}]% | max={self.max_trades_per_day} "
            f"cutoff={self.entry_cutoff_trades} | stop(day)={day_stop} | "
            f"no-new>{self.no_new_entries_after_cst} | flat>{self.exit_all_by_cst} CST"
        )

    def tp_for_trade(self, i: int) -> float:
        if i < len(self.tp_targets):
            return self.tp_targets[i]
        return self.tp_targets[-1]

    def no_new_entries_time(self) -> time:
        return _parse_time_str(self.no_new_entries_after_cst)

    def exit_all_time(self) -> time:
        return _parse_time_str(self.exit_all_by_cst)


def _parse_time_str(value: str) -> time:
    parts = value.split(":")
    if len(parts) != 2:
        return time(13, 45)
    hour = int(parts[0])
    minute = int(parts[1])
    return time(hour=hour, minute=minute)


def policy_time_hint(policy: TradePolicy, now: datetime) -> str | None:
    now_t = now.time()
    cutoff = policy.no_new_entries_time()
    exit_time = policy.exit_all_time()
    cutoff_dt = datetime.combine(now.date(), cutoff)
    exit_dt = datetime.combine(now.date(), exit_time)
    now_dt = datetime.combine(now.date(), now_t)
    if now_t >= exit_time:
        return "Exit window reached: plan to be flat."
    if now_t >= cutoff:
        return "After cutoff: no new entries."
    if (cutoff_dt - now_dt).total_seconds() <= 1800:
        return "Exit window approaching: plan to be flat."
    return None
