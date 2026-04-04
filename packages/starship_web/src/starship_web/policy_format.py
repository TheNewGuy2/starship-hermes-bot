from __future__ import annotations

import os
from typing import Iterable

from starship_shared.policy import TradePolicy

_R_DOLLARS_RAW = os.getenv("STARSHIP_R_DOLLARS")
_R_DOLLARS = float(_R_DOLLARS_RAW) if _R_DOLLARS_RAW else None


def _fmt_dollars(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:,.0f}"


def _tp_dollars(tp_targets: Iterable[float]) -> str:
    dollars = [_fmt_dollars(t * _R_DOLLARS) for t in tp_targets]
    return ", ".join(dollars)


def format_policy_lines(policy: TradePolicy) -> list[str]:
    lines: list[str] = []
    lines.append(policy.summary_line())
    if _R_DOLLARS is not None:
        lines.append(
            "TP $: [{tps}] | stop(day) {day} | stop(trade) {trade}".format(
                tps=_tp_dollars(policy.tp_targets),
                day=_fmt_dollars(
                    policy.daily_stop_r * _R_DOLLARS
                    if policy.daily_stop_r is not None
                    else None
                ),
                trade=_fmt_dollars(
                    policy.per_trade_stop_r * _R_DOLLARS
                    if policy.per_trade_stop_r is not None
                    else None
                ),
            )
        )
    lines.append(
        f"Flat by {policy.exit_all_by_cst} CST (no new entries after {policy.no_new_entries_after_cst})"
    )
    return lines
