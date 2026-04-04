# src/bot/market_hours.py
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo


def in_rth(now: datetime, tz: ZoneInfo) -> bool:
    local = now.astimezone(tz)
    t = local.time()
    start = time(8, 30)
    end = time(15, 0)
    return start <= t <= end
